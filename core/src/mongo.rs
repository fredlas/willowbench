// mongo.rs
use std::collections::HashMap;
use mongodb::{bson, error::Error as MongoError};
use mongodb::results::{InsertOneResult, UpdateResult};
use bson::Bson;
use std::time::{SystemTime, UNIX_EPOCH};
use mongodb::{IndexModel, options::IndexOptions};

use crate::utils::JobId;
use crate::{debug};

#[derive(serde::Serialize, serde::Deserialize, Clone)]
pub struct MongoJob {
  pub job_id: String,
  pub nickname: String,
  pub workflow_name: String,
  pub customer_id: i32,
  pub owning_useremail: String,
  // pub started_sse: i64,
  // pub last_activity_sse: i64,
  pub status: String,
  pub comment: String,
  pub region: String,
  pub inputs: HashMap<String, String>,
  pub outputs: HashMap<String, String>,
  pub task_logs: HashMap<String, TaskLogs>,
}

#[derive(serde::Serialize, serde::Deserialize, Clone)]
pub struct MongoCustomer {
  pub customer_id: i32,
  pub customer_name: String,
  pub last_sub_payment_sse: i64,
  pub credits_balance: i32,
  pub billing_type: String,
  pub cloud_region: String,
  pub admin_email: String,
  pub aws_id: String,
  pub subnet_id: String,
  pub security_group_id: String,
}

#[derive(serde::Serialize, serde::Deserialize, Clone)]
pub struct TaskLogs {
    pub stdout: String,
    pub stderr: String,
}

// TODO eventually add task version, too
pub fn make_cache_key(task_name: &str, inputs: &HashMap<String, String>) -> String
{
    let mut inputs_vec: Vec<_> = inputs.iter().collect();
    inputs_vec.sort_by(|a, b| a.0.cmp(b.0));

    let inputs_str = inputs_vec
        .into_iter()
        .map(|(k, v)| format!("{}:{}", k, v))
        .collect::<Vec<String>>()
        .join(",");

    format!("{}:{}", task_name, inputs_str)
}

#[allow(dead_code)]
#[derive(Clone)]
pub struct WillowMongo
{
  mongo_client: mongodb::Client,
  pub mdb: mongodb::Database,
  pub jobs_col: mongodb::Collection<bson::Document>, // <MongoJob>
  pub customers_col: mongodb::Collection<bson::Document>, // <MongoCustomer>
  pub users_col: mongodb::Collection<bson::Document>,
  pub tasks_col: mongodb::Collection<bson::Document>,
}

impl WillowMongo {
pub async fn new(mongo_client: mongodb::Client) -> Result<WillowMongo, MongoError>
{
  let mdb = mongo_client.database("willow_database");
  let jobs_col = mdb.collection("jobs");
  let customers_col = mdb.collection("customers");
  let users_col = mdb.collection("users");
  let tasks_col = mdb.collection("tasks");

  // Ensure a unique index exists for job_id.
  let options = IndexOptions::builder().unique(true).build();
  let model = IndexModel::builder()
      .keys(bson::doc! { "job_id": 1 })
      .options(options)
      .build();
  jobs_col.create_index(model).await?;

  Ok(WillowMongo { mongo_client, mdb, jobs_col, customers_col, users_col, tasks_col })
}

pub async fn insert_job(&self, job: MongoJob) -> Result<InsertOneResult, MongoError>
{
  let now_sse = SystemTime::now().duration_since(UNIX_EPOCH)
                                  .expect("Time went backwards").as_secs();
  let doc = bson::doc! {
      "job_id": job.job_id,
      "nickname": job.nickname,
      "workflow_name": job.workflow_name,
      "customer_id": job.customer_id,
      "owning_useremail": job.owning_useremail,
      "started_sse": Bson::Int64(now_sse as i64),
      "last_activity_sse": Bson::Int64(now_sse as i64),
      "status": job.status,
      "status_details": "",
      "comment": job.comment,
      "region": job.region,
      "inputs": bson::to_document(&job.inputs)?,
      "outputs": bson::to_document(&job.outputs)?,
      "task_logs": bson::to_document(&job.task_logs)?,
  };

  self.jobs_col.insert_one(doc)
  .await
}

pub async fn region_from_job_id(&self, job_id: &str) -> Result<String, String>
{
    let doc = self.jobs_col.find_one(bson::doc! { "job_id": job_id })
        .await
        .map_err(|e| format!("mongo error: {}", e))?
        .ok_or_else(|| format!("job_id {} not found in mongo", job_id))?;

    mongo_get_string(&doc, "region")
}

pub async fn update_status(&self, job_id: &JobId, status: &str, details: &str)
-> Result<UpdateResult, MongoError>
{
  let now_sse = SystemTime::now().duration_since(UNIX_EPOCH)
                                 .expect("Time went backwards").as_secs();
  let mongo_filter = bson::doc! { "job_id": job_id };

  let mongo_update =
      if details.len() > 1
      {
        debug!(job_id, "updating status_details to {}", details);
        bson::doc! { "$set": { "status": status,
                               "status_details": details,
                               "last_activity_sse": Bson::Int64(now_sse as i64) }}
      }
      else
      {
        bson::doc! { "$set": { "status": status,
                               "last_activity_sse": Bson::Int64(now_sse as i64) }}
      };

  self.jobs_col.update_one(mongo_filter, mongo_update)
  .await
}

pub async fn update_failed_status(&self, job_id: &JobId, reason: &str, error_details: Option<&str>)
-> Result<UpdateResult, MongoError>
{
    let now_sse = SystemTime::now().duration_since(UNIX_EPOCH).expect("Time went backwards").as_secs();
    let mongo_filter = bson::doc! { "job_id": job_id };

    let mut set_doc = bson::doc! {
        "status": "failed",
        "status_details": reason,
        "last_activity_sse": Bson::Int64(now_sse as i64)
    };
    if let Some(details) = error_details {
        if !details.is_empty() {
            set_doc.insert("error_details", details);
        }
    }

    let mongo_update = bson::doc! { "$set": set_doc };
    self.jobs_col.update_one(mongo_filter, mongo_update).await
}

pub async fn finish_job(&self, job_id: &JobId, outputs: HashMap<String, String>)
-> Result<UpdateResult, MongoError>
{
  let now_sse = SystemTime::now().duration_since(UNIX_EPOCH)
                                 .expect("Time went backwards").as_secs();
  let mongo_filter = bson::doc! { "job_id": job_id };
  let outputs_bson = bson::to_document(&outputs)?;

  let mongo_update = bson::doc! { "$set": { "status": "completed", "outputs": outputs_bson,
                                            "last_activity_sse": Bson::Int64(now_sse as i64) } };
  self.jobs_col.update_one(mongo_filter, mongo_update)
  .await
}

pub async fn update_task_logs(&self, job_id: &JobId, task_name: &str, task_logs: TaskLogs)
-> Result<UpdateResult, MongoError>
{
  let mongo_filter = bson::doc! { "job_id": job_id };
  let task_logs_bson = bson::to_document(&task_logs).map_err(|e| MongoError::custom(e.to_string()))?;
  let mongo_update = bson::doc! { "$set": { format!("task_logs.{}", task_name): task_logs_bson } };
  self.jobs_col.update_one(mongo_filter, mongo_update)
  .await
}

pub async fn get_customer_from_id(&self, id: i32) -> Result<MongoCustomer, String>
{
  match self.customers_col.find_one(bson::doc! {"customer_id": id}).await
  {
    Ok(Some(doc)) =>
    {
      Ok(MongoCustomer {
          customer_id: mongo_get_i32(&doc, "customer_id")?,
          customer_name: mongo_get_string(&doc, "customer_name")?,
          last_sub_payment_sse: mongo_get_i64(&doc, "last_sub_payment_sse")?,
          credits_balance: mongo_get_i32(&doc, "credits_balance")?,
          billing_type: mongo_get_string(&doc, "billing_type")?,
          cloud_region: mongo_get_string(&doc, "cloud_region")?,
          admin_email: mongo_get_string(&doc, "admin_email")?,
          aws_id: mongo_get_string(&doc, "aws_id")?,
          subnet_id: mongo_get_string(&doc, "subnet_id")?,
          security_group_id: mongo_get_string(&doc, "security_group_id")?,
      })
    }
    Ok(None) => { Err(format!("customer {} not found in mongo", id)) }
    Err(e) => { Err(format!("mongo error while looking up customer {}: {}", id, e)) }
  }
}

pub async fn get_customer_from_useremail(&self, useremail: &str) -> Result<MongoCustomer, String>
{
  match self.users_col.find_one(bson::doc! {"useremail": useremail}).await
  {
    Ok(Some(doc)) =>
    {
      let cust_id_res = doc.get_i32("customer_id");
      if let Err(e) = cust_id_res {
        return Err(format!("error {} getting customer_id field from user {}'s record", e, useremail)); }

      self.get_customer_from_id(cust_id_res.unwrap()).await
    }
    Ok(None) => { Err(format!("{} not found in mongo", useremail)) }
    Err(e) => { Err(format!("mongo error while looking up user {}: {}", useremail, e)) }
  }
}

pub async fn insert_task(&self, job_id: &str, task_name: &str, region: &str, instance_id: &str,
                         inputs: &HashMap<String, String>)
-> Result<InsertOneResult, MongoError>
{
  let now_sse = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() as i64;
  let cache_key = make_cache_key(task_name, inputs);
  let doc = bson::doc! {
    "job_id": job_id, // this is original job_id - the one that owned the task when the task actually ran.
                      // this tasks_col entry can later be used as a cached result by other jobs.
                      // (at which point job_id should be irrelevant, so ok to be inconsistent).
    "task_name": task_name,
    "region": region,
    "instance_id": instance_id,
    "start_time": Bson::Int64(now_sse),
    "status": "running",
    "inputs": bson::to_document(inputs)?,
    "cache_key": cache_key,
  };
  self.tasks_col.insert_one(doc)
  .await
}

pub async fn update_task_completion(&self, job_id: &JobId, task_name: &str, pickled_outputs: &str)
-> Result<UpdateResult, MongoError>
{
  let now_sse = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() as i64;
  let mongo_filter = bson::doc! { "job_id": job_id, "task_name": task_name };

  let mongo_update = bson::doc! { "$set": bson::doc! {
      "status": "done",
      "finish_time": Bson::Int64(now_sse),
      "pickled_outputs": pickled_outputs, } };
  self.tasks_col.update_one(mongo_filter, mongo_update)
  .await
}

pub async fn update_failed_task(&self, job_id: &JobId, task_name: &str, reason: &str, error_details: Option<&str>)
-> Result<UpdateResult, MongoError>
{
    let now_sse = SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs() as i64;
    let mongo_filter = bson::doc! { "job_id": job_id, "task_name": task_name };

    let mut set_doc = bson::doc! {
        "status": "failed",
        "finish_time": Bson::Int64(now_sse),
        "status_details": reason,
    };
    if let Some(details) = error_details {
        if !details.is_empty() {
            set_doc.insert("error_details", details);
        }
    }
    let mongo_update = bson::doc! { "$set": set_doc };
    self.tasks_col.update_one(mongo_filter, mongo_update)
    .await
}

pub async fn find_task_by_cache_key(&self, cache_key: &str) -> Result<Option<bson::Document>, MongoError>
{
  let filter = bson::doc! { "cache_key": cache_key, "status": "done" };
  self.tasks_col.find_one(filter)
  .await
}

pub async fn get_tasks_for_job(&self, job_id: &str) -> Result<Vec<bson::Document>, String>
{
  let filter = bson::doc! { "job_id": job_id };

  let find_res = self.tasks_col.find(filter)
  .await;
  if let Err(e) = find_res { return Err(format!("find job in mongo failed: {}", e.to_string())); }
  let mut cursor = find_res.unwrap();

  let mut tasks = Vec::new();
  while cursor.advance().await.map_err(|e| e.to_string())?
  {
    let raw_doc = cursor.current();
    match bson::Document::try_from(raw_doc)
    {
      Ok(doc) => tasks.push(doc),
      Err(e) => { return Err(format!("Error converting RawDocument to Document: {}", e)); }
    }
  }
  Ok(tasks)
}

} // impl WillowMongo



fn mongo_get_i32(doc: &bson::Document, key: &str) -> Result<i32, String>
{
  match doc.get_i32(key)
  {
    Ok(x) => Ok(x),
    Err(e) => Err(format!("got error ''{}'' when accessing {}", e, key))
  }
}

fn mongo_get_i64(doc: &bson::Document, key: &str) -> Result<i64, String>
{
  match doc.get_i64(key)
  {
    Ok(x) => Ok(x),
    Err(_e) =>
    {
      // mongo distinguishes i32 and i64, and won't just give you an i32 as an i64...
      let res = mongo_get_i32(doc, key);
      if let Err(s) = res { return Err(s); }
      let the_i32 = res.unwrap();
      Ok(the_i32 as i64)
    }
  }
}

fn mongo_get_string(doc: &bson::Document, key: &str) -> Result<String, String>
{
  match doc.get_str(key)
  {
    Ok(x) => Ok(x.to_string()),
    Err(e) => Err(format!("got error ''{}'' when accessing {}", e, key))
  }
}
