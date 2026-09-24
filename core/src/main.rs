use actix_web::{web, App, HttpRequest, HttpResponse, HttpServer, Responder};
use std::collections::HashMap;
use std::env;
use std::path::PathBuf;
use std::time::{SystemTime, UNIX_EPOCH};
use std::io::{stderr, stdout, Write};
use serde::Deserialize;
use serde_json::json;
use simplelog::SimpleLogger;
use mongodb::{bson};

mod herder_client;
mod mongo;
mod subprocess_manager;
mod taskmaster;
mod utils;

use utils::{JobId, running_in_dev, crash, verify_task_secret_hmac};
use crate::mongo::{MongoJob, WillowMongo, TaskLogs};
use subprocess_manager::FRONTEND_BASE_URL;
use crate::herder_client::send_to_herder;
use crate::taskmaster::cancel_task;


#[derive(Debug, Deserialize)]
pub struct TaskDone
{
  pub job_id: String,
  pub task_name: String,
  pub secret: String,
  pub status: String,
  pub error_details: Option<String>,
  pub stdout: String,
  pub stderr: String,
  #[serde(default)]
  pub cloudpaths: HashMap<String, String>,
  pub file_contents: Option<serde_json::Value>,
  pub include_cmd_stdout: bool,
  pub glob_result_lists: Option<serde_json::Value>,
}
async fn task_done(data: web::Json<TaskDone>, willow_mongo: web::Data<WillowMongo>, _req: HttpRequest)
-> impl Responder
{
  let d = data.into_inner();

  // this is a task outpost; it needs to authenticate
  if !verify_task_secret_hmac(&d.secret, &d.job_id, &d.task_name) {
    return HttpResponse::Forbidden().body("failed verification"); }

  if d.status == "OK" { info!(d.job_id, "task {} ended with status: {}", d.task_name, d.status); }
  else
  {
    warn!(d.job_id, "task {} ended with status: {}", d.task_name, d.status);
    let reason = format!("Task '{}' failed with status: {}", d.task_name, d.status);

    let update_job_failed_fut = willow_mongo.update_failed_status(&d.job_id, &reason, d.error_details.as_deref());
    let update_task_failed_fut = willow_mongo.update_failed_task(&d.job_id, &d.task_name, &reason, d.error_details.as_deref());
    if let Err(e) = update_job_failed_fut.await
    {
      error!(d.job_id, "Failed to set failed details in mongo for job {}: {}", d.status, e);
      return HttpResponse::FailedDependency().body(format!("Failed to set failure for job in mongo to {}: {}",
                                                           d.status, e));
    }
    if let Err(e) = update_task_failed_fut.await
    {
        error!(d.job_id, "Failed to set failed status for task {} in mongo: {}", d.task_name, e);
        return HttpResponse::FailedDependency().body(format!("Failed to set failure for task {} in mongo: {}",
                                                             d.task_name, e));
    }
  }

  info!(d.job_id, "telling widdler to mark task {} finished with status {}", d.task_name, d.status);

  let mut msg_json = serde_json::json!({"the_fn": "task_done",
                                        "job_id": d.job_id.clone(),
                                        "task_call_as": d.task_name,
                                        "status": d.status});
  if let Some(ref details) = d.error_details {
    msg_json["error_details"] = json!(details); }
  if let Some(ref file_contents) = d.file_contents {
    msg_json["file_contents"] = file_contents.clone(); }
  if d.include_cmd_stdout {
    msg_json["cmd_stdout"] = json!(d.stdout); }
  if let Some(ref glob_result_lists) = d.glob_result_lists {
    msg_json["glob_result_lists"] = glob_result_lists.clone(); }
  if !d.cloudpaths.is_empty() {
    msg_json["cloudpaths"] = json!(d.cloudpaths); }

  let update_task_logs_fut =
    willow_mongo.update_task_logs(&d.job_id, &d.task_name,
                                  TaskLogs { stdout: d.stdout.clone(), stderr: d.stderr, });

  if let Err(e) = send_to_herder(msg_json).await
  {
    error!(d.job_id, "Failed to send task_done to Herder: {}", e);
    return HttpResponse::FailedDependency().body(format!("Failed to send task_done to Herder: {}", e));
  }
  if let Err(e) = update_task_logs_fut.await
  {
    error!(d.job_id, "Failed to update task logs in mongo: {}", e);
    return HttpResponse::InternalServerError().body(format!("Failed to update task logs in mongo: {}", e));
  }
  HttpResponse::Ok().body("ok")
}

// Widdler needs to be told "files_available" for the available-from-the-start files of the initial
// workflow inputs, same as the intermediate ones, so we have to do that here.
fn prepare_wf_input_files_available_msg(inputs: &HashMap<String, String>,
                                        input_types: &HashMap<String, String>, job_id: &str,
                                        workflow_name: &str) ->
Result<Option<serde_json::Value>, String>
{
  let mut cloudpaths = HashMap::new();
  for (input_name, input_value_raw) in inputs
  {
    let input_value = input_value_raw.trim();
    if let Some(input_type) = input_types.get(input_name) {
      info!(job_id, "input_name {}, input_value {}, input_type {}", input_name , input_value, input_type);
      if input_type == "File"
      {
        if !input_value.is_empty() && !input_value.starts_with("s3://") {
          return Err(format!("Invalid input format for '{}' - must be an S3 URI", input_name)); }
        let fqvn = format!("{}.{}", workflow_name, input_name);
        cloudpaths.insert(fqvn, input_value.to_string());
      }
      else if input_type == "Struct" || input_type == "ArrayFile"
      {
        let name_prefix = format!("{}.{}", workflow_name, input_name);
        if input_value == "" {
          continue; }
        let j: serde_json::Value = match serde_json::from_str(input_value) {
            Ok(val) => val,
            Err(e) => return Err(format!("Invalid JSON for {} input '{}': {}", input_type, input_name, e)),
        };

        fn find_s3_paths_recursive(json_val: &serde_json::Value, path: &str, cloudpaths: &mut HashMap<String, String>)
        {
          match json_val
          {
            serde_json::Value::Object(map) =>
            {
              for (k, v) in map {
                find_s3_paths_recursive(v, &format!("{}.{}", path, k), cloudpaths); }
            },
            serde_json::Value::Array(arr) =>
            {
              for (i, v) in arr.iter().enumerate() {
                find_s3_paths_recursive(v, &format!("{}_WILLOWindex{}", path, i), cloudpaths); }
            },
            serde_json::Value::String(s) =>
            {
              if s.starts_with("s3://") {
                cloudpaths.insert(path.to_string(), s.to_string()); }
            },
            _ => {} // Ignore other types
          }
        }
        find_s3_paths_recursive(&j, &name_prefix, &mut cloudpaths);
      }
      else if input_type.contains("ArrayArrayFile") {
        return Err(format!("nested file arrays not yet supported")); }
    }
    else {
      return Err(format!("Missing type information for input '{}'", input_name)); }
  }

  if !cloudpaths.is_empty()
  {
    Ok(Some(serde_json::json!({
        "the_fn": "files_available",
        "job_id": job_id,
        "cloudpaths": cloudpaths
    })))
  }
  else { Ok(None) }
}

fn build_cloud_bucket_name(customer_id: i32, region: &str) -> String
{
  if customer_id == 101024 && region == "us-west-1" { "willowbench-test-tasks-uswest1".to_string() }
  else if customer_id == 101030 && region == "us-east-1" { "willowtestuploads".to_string() }
  else { format!("willowbench-tasks-{}-{}", customer_id, region) }
}

async fn start_job(job_id: JobId, workflow_name: String, customer_id: i32,
                   owning_useremail: String, vars: HashMap<String, String>,
                   region: String, willow_mongo: &WillowMongo) ->
Result<(), String>
{
  let cloudbucket = build_cloud_bucket_name(customer_id, &region);

  let mongo_job = MongoJob {
      job_id: job_id.clone(),
      nickname: "".to_string(),
      workflow_name: workflow_name.clone(),
      customer_id: customer_id,
      owning_useremail: owning_useremail.clone(),
      status: "pending".to_string(),
      comment: "".to_string(),
      region: region,
      inputs: vars.clone(),
      outputs: HashMap::new(),
      task_logs: HashMap::new()
  };

  debug!(job_id, "inserting new job into mongo");
  let mongo_fut = willow_mongo.insert_job(mongo_job);

  debug!(job_id, "sending widdler a start_job");
  let herder_fut = send_to_herder(serde_json::json!({"the_fn": "start_job",
                                                     "job_id": job_id,
                                                     "workflow_name": workflow_name,
                                                     "useremail": owning_useremail,
                                                     "cloudbucket": cloudbucket,
                                                     "inputs": vars}));
  if let Err(e) = mongo_fut.await {
    return Err(format!("Failed to insert job into mongo: {}", e));
  }
  if let Err(e) = herder_fut.await {
    return Err(format!("Failed to send start_job to Herder: {}", e));
  }
  Ok(())
}





// These are what come into the /run_willow_workflow handler. They are equivalent to
// a JobMsg delivering newly available vars.
// It has the workflow_name and customer_id fields beyond what JobMsg has,
// since it is responsible for causing the job to start.
#[derive(Debug, Deserialize)]
pub struct UserInputs
{
  pub job_id: String,
  pub workflow_name: String,
  pub customer_id: i32,
  pub owning_useremail: String,
  // key is variable name, value is value.
  // whatever ultimately uses the value has to know from workflow schema what
  // each named input is, to know whether to convert to a number, or treat as a
  // filename, or leave as a string.
  pub inputs: HashMap<String, String>,
  pub input_types: HashMap<String, String>,
}
async fn run_willow_workflow(data: web::Json<UserInputs>, willow_mongo: web::Data<WillowMongo>, _req: HttpRequest) -> impl Responder
{
  let d = data.into_inner();
  info!(d.job_id, "starting /run_willow_workflow");

  // NOTE: authentication: used to check is_loopback to be sure only frontend can use these various
  //       API endpoints. Now have a secgrp in AWS walling off 7770-7779 on core machine from all
  //       but frontend. so the check is no longer needed (and now supports being on different VMs)
  //       (same is true of all these API endpoint handlers)

  info!(d.job_id, "/run_willow_workflow starting get_customer_from_id");

  let cust_res = willow_mongo.get_customer_from_id(d.customer_id).await;
  if let Err(s) = cust_res
  {
    error!(d.job_id, "get_customer_from_id failed: {}", s);
    return HttpResponse::BadRequest().body(format!("invalid customer_id {}", d.customer_id));
  }
  let cust = cust_res.unwrap();
  info!(d.job_id, "/run_willow_workflow: get_customer_from_id done");

  let credits_balance = cust.credits_balance;
  let cloud_region = cust.cloud_region;
  if credits_balance < 0
  {
    // TODO make sure this message clearly propagates to the user
    return HttpResponse::Forbidden().body(
      "Sorry, your account is not in good standing! Please have your admin user purchase more credits.");
  }

  let now_sse = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("Time went backwards")
        .as_secs() as i64;
  let _one_month_ago = now_sse - 31 * 24 * 60 * 60;

  // TODO uncomment so they actually have to pay us!
  // if cust.last_sub_payment_sse < one_month_ago
  // {
  //   return HttpResponse::Forbidden().body(
  //     "Sorry, your account is not in good standing! Please have your admin user renew the subscription.");
  // }
  info!(d.job_id, "/run_willow_workflow: preparing file available messages");
  let msgs_res = prepare_wf_input_files_available_msg(&d.inputs, &d.input_types, &d.job_id, &d.workflow_name);
  if let Err(err) = msgs_res { return HttpResponse::BadRequest().body(err); }
  let maybe_fa_message = msgs_res.unwrap();
  info!(d.job_id, "/run_willow_workflow: done preparing file available messages");

  let trimmed_inputs =
  {
    d.inputs.iter().map(|(k, v)|
    {
      let should_trim = match d.input_types.get(k)
      {
        Some(value_type) => value_type.as_str() != "String",
        None => panic!("job {}: input {} missing its type", d.job_id, k)
      };
      if should_trim {
        (k.clone(), v.trim().to_string()) }
      else {
        (k.clone(), v.clone()) }
    })
    .collect()
  };
  info!(d.job_id, "job requested with inputs {}",
        d.inputs.keys().map(|k| k.to_string()).collect::<Vec<_>>().join(", "));

  // Call start_job before sending files_available messages, because Widdler must know the job exists
  // for files_available to have an effect. (This works because sending through the herder guarantees
  // the message has gone to Widdler stdin by the time the HTTP reply returns, which we await).
  let start_job_fut = start_job(d.job_id.clone(), d.workflow_name, d.customer_id, d.owning_useremail,
                                trimmed_inputs, cloud_region, &willow_mongo);
  if let Err(e) = start_job_fut.await
  {
    error!(d.job_id, "Failed to start job: {}", e);
    return HttpResponse::InternalServerError().body(format!("Failed to start job: {}", e));
  }

  if let Some(fa_msg) = maybe_fa_message
  {
    if let Err(e) = send_to_herder(fa_msg).await
    {
      error!(d.job_id, "Failed to send files_available to Herder: {}", e);
      return HttpResponse::InternalServerError().body(format!("Failed to notify file available: {}", e));
    }
  }
  HttpResponse::Ok().body("ok")
}

#[derive(Debug, Deserialize)]
pub struct CancelRequest {
    pub job_id: String,
}
async fn cancel_workflow(willow_mongo: web::Data<WillowMongo>, data: web::Json<CancelRequest>) -> impl Responder
{
  let job_id = data.job_id.clone();
  info!(job_id, "workflow cancellation initiated");

  let mongo_cancel_fut = willow_mongo.update_status(&job_id, "cancelled", "");

  let herder_cancel_fut = send_to_herder(serde_json::json!({"the_fn": "cancel_workflow",
                                                            "job_id": job_id.clone()}));
  // Get tasks from MongoDB and cancel them
  let tasks_res = willow_mongo.get_tasks_for_job(&job_id).await;
  if let Err(e) = tasks_res
  {
    error!(job_id, "Failed to get tasks list from mongo: {}", e);
    return HttpResponse::InternalServerError().body(format!("Failed to get tasks list from mongo: {}", e));
  }
  let mut task_cancel_futs = Vec::new();
  for task in tasks_res.unwrap()
  {
    if let (Some(instance_id), Some(region), Some(task_name)) = (task.get_str("instance_id").ok(),
                                                                 task.get_str("region").ok(),
                                                                 task.get_str("task_name").ok())
    {
      task_cancel_futs.push(cancel_task(instance_id.to_string(), region.to_string(),
                                        &job_id, task_name.to_string()));
    }
  }
  for fut in task_cancel_futs
  {
    if let Err(e) = fut.await
    {
      error!(job_id, "{}", e);
      return HttpResponse::FailedDependency().body(format!("{}", e));
    }
  }
  if let Err(e) = mongo_cancel_fut.await
  {
    error!(job_id, "Failed to update status in mongo: {}", e);
    return HttpResponse::InternalServerError().body(format!("Failed to update status in mongo: {}", e));
  }
  if let Err(e) = herder_cancel_fut.await
  {
    error!(job_id, "Failed to send cancel_workflow to Herder: {}", e);
    return HttpResponse::FailedDependency().body(format!("Failed to send cancel_workflow to Herder: {}", e));
  }
  HttpResponse::Ok().body("ok")
}

async fn default_route(req: HttpRequest) -> impl Responder
{
  let method = req.method().as_str();
  let uri = req.uri().to_string();
  let headers = req.headers().iter().map(|(name, value)| {
    format!("{}: {}", name, value.to_str().unwrap_or(""))
  }).collect::<Vec<_>>().join("\n");
  eprintln!("handling default route: {} {}\nHeaders:\n{}", method, uri, headers);

  if uri.contains("begin_shutdown") && method == "GET" {
    return HttpResponse::Unauthorized().body("you are forgetting -X POST"); }
  HttpResponse::Unauthorized().body("no")
}

async fn health_check(_req: HttpRequest) -> impl Responder
{
  HttpResponse::Ok().body("ok")
}

#[allow(unreachable_code)]
async fn begin_shutdown() -> impl Responder
{
  info!("none", "Received begin_shutdown, initiating NOT-SO-graceful shutdown lol bye");
  eprintln!("CORE EXITING IN RESPONSE TO /begin_shutdownzzz_shutdown_yes_please");
  stdout().flush().unwrap(); stderr().flush().unwrap();
  std::process::exit(0);
  HttpResponse::Ok().body("ok")
}

async fn widdler_run_task(data: web::Json<serde_json::Value>, willow_mongo: web::Data<WillowMongo>,
                          _req: HttpRequest) -> impl Responder
{
  let message = data.into_inner();
  info!(message["job_id"], "doing a run_task");
  if let Err(e) = subprocess_manager::process_widdler_message(&message, &willow_mongo).await
  {
    error!(message["job_id"], "run_task from widdler failed: {}", e);
    return HttpResponse::BadRequest().body(e);
  }
  info!(message["job_id"], "run_task done");
  HttpResponse::Ok().body("ok")
}

async fn widdler_job_failed(data: web::Json<serde_json::Value>, willow_mongo: web::Data<WillowMongo>,
                            _req: HttpRequest) -> impl Responder
{
  let message = data.into_inner();
  if let Err(e) = subprocess_manager::process_widdler_message(&message, &willow_mongo).await
  {
    error!(message["job_id"], "job_failed from widdler failed: {}", e);
    return HttpResponse::BadRequest().body(e);
  }
  HttpResponse::Ok().body("ok")
}

async fn widdler_job_done(data: web::Json<serde_json::Value>, willow_mongo: web::Data<WillowMongo>,
                          _req: HttpRequest) -> impl Responder
{
  let message = data.into_inner();
  if let Err(e) = subprocess_manager::process_widdler_message(&message, &willow_mongo).await
  {
    error!(message["job_id"], "job_done from widdler failed: {}", e);
    return HttpResponse::BadRequest().body(e);
  }
  HttpResponse::Ok().body("ok")
}

async fn widdler_task_cache_store(data: web::Json<serde_json::Value>, willow_mongo: web::Data<WillowMongo>,
                                  _req: HttpRequest) -> impl Responder
{
    let message = data.into_inner();
    if let Err(e) = subprocess_manager::process_widdler_message(&message, &willow_mongo).await
    {
        error!(message["job_id"], "task_cache_store from widdler failed: {}", e);
        return HttpResponse::BadRequest().body(e);
    }
    HttpResponse::Ok().body("ok")
}


#[derive(Deserialize)]
pub struct SizeQuery
{
  pub the_fn: String,
  pub job_id: String,
  #[serde(default)]
  pub cloudpaths: Vec<String>,
}
async fn widdler_size_query(data: web::Json<SizeQuery>, willow_mongo: web::Data<WillowMongo>,
                          _req: HttpRequest) -> impl Responder
{
  let d = data.into_inner();

  let region_res = willow_mongo.region_from_job_id(&d.job_id)
  .await;
  if let Err(e) = region_res {
    return HttpResponse::InternalServerError().body(format!("failed to look up region: {}", e));
  }
  let region = region_res.unwrap();

  let output_res = tokio::process::Command::new("python3")
      .arg("../utils/s3_sizer.py")
      .arg("--aws-region")
      .arg(region)
      .args(d.cloudpaths)
      .output()
      .await
      .map_err(|e| format!("failed to execute size query: {}", e));
  if let Err(e) = output_res {
    return HttpResponse::InternalServerError().body(e);
  }
  let output = output_res.unwrap();

  if !output.status.success()
  {
    let stderr = String::from_utf8_lossy(&output.stderr);
    warn!(d.job_id, "size query failed: {}", stderr);
    return HttpResponse::FailedDependency().body(format!("s3_sizer.py script failed: {}", stderr));
  }

  let sizes: HashMap<String, u64> = serde_json::from_slice(&output.stdout).unwrap();
  let mut size_response = HashMap::new();
  for (k,v) in sizes {
    size_response.insert(k, v.to_string()); }

  let herder_res = send_to_herder(serde_json::json!({
    "the_fn": "size_response",
    "job_id": d.job_id,
    "sizes": size_response,
  })).await;
  if let Err(e) = herder_res {
    return HttpResponse::FailedDependency().body(format!("failed to send size_response: {}", e));
  }
  HttpResponse::Ok().body("ok")
}

#[actix_web::main]
async fn main() -> std::io::Result<()>
{
  let simplelog_config = simplelog::ConfigBuilder::new()
      .set_max_level(simplelog::LevelFilter::Off)
      .set_target_level(simplelog::LevelFilter::Off)
      .set_time_format_custom(time::macros::format_description!("[year]-[month]-[day] [hour]:[minute]:[second]"))
      .build();
  if let Err(e) = SimpleLogger::init(if running_in_dev() {simplelog::LevelFilter::Info}
                                                    else {simplelog::LevelFilter::Info}, simplelog_config)
  {
    eprintln!("Error initializing simple_logger: {}", e);
    return Err(std::io::Error::new(std::io::ErrorKind::InvalidInput, "Error initializing simple_logger"));
  }

  let args: Vec<String> = env::args().collect();
  if args.len() < 4 || args[2] != "--port"
  {
    eprintln!("Usage: {} <repo_path> --port <port> [--dev]", args[0]);
    eprintln!("Invoked with: {}", args.join(" "));
    return Err(std::io::Error::new(std::io::ErrorKind::InvalidInput, "Wrong cmdline args"));
  }
  let repo_path = PathBuf::from(&args[1]);
  if !repo_path.exists()
  {
    eprintln!("Error: Repo path '{}' does not exist.", repo_path.display());
    return Err(std::io::Error::new(std::io::ErrorKind::NotFound, "Repo path not found"));
  }
  let port_num =
      if let Ok(num) = args[3].parse::<u16>() { num }
      else { return Err(std::io::Error::new(std::io::ErrorKind::InvalidInput,
             "Invalid --port ".to_string() + &args[3])); };
  info!("none", "frontend URL base for task reporting is {}", *FRONTEND_BASE_URL);

  let mongo_client_res = mongodb::Client::with_uri_str(
      "mongodb://127.0.0.1:27017/?directConnection=true&serverSelectionTimeoutMS=2000&appName=willowcore")
  .await;
  if let Err(ref err) = mongo_client_res {
    crash(format!("Error connecting to mongo: {}", err)); }
  let mongo_client = mongo_client_res.unwrap();
  // Ping the database to ensure connection is established
  let ping_res = mongo_client.database("willow_database").run_command(bson::doc! { "ping": 1 })
  .await;
  if let Err(ref e) = ping_res {
    crash(format!("Error pinging mongo: {}", e)); }
  info!("none", "successfully pinged mongo");
  let willow_mongo = WillowMongo::new(mongo_client).await.unwrap();
  if running_in_dev() {
    eprintln!("core Actix HttpServer now starting, DEV"); }
  else {
    eprintln!("core Actix HttpServer now starting, PROD"); }

  let server = HttpServer::new(move ||
  {
    App::new()
      .app_data(web::Data::new(willow_mongo.clone()))
      .route("/run_willow_workflow", web::post().to(run_willow_workflow))
      .route("/task/task_done", web::post().to(task_done))
      .route("/cancel_workflow", web::post().to(cancel_workflow))
      .route("/begin_shutdownzzz_shutdown_yes_please", web::post().to(begin_shutdown))
      .route("/health", web::route().to(health_check))
      .route("/widdler/run_task", web::post().to(widdler_run_task))
      .route("/widdler/job_failed", web::post().to(widdler_job_failed))
      .route("/widdler/job_done", web::post().to(widdler_job_done))
      .route("/widdler/task_cache_store", web::post().to(widdler_task_cache_store))
      .route("/widdler/size_query", web::post().to(widdler_size_query))
      .default_service(web::route().to(default_route))
  })
  .bind(format!("[::]:{port_num}"))?
  .run();
  info!("none", "Core Actix server started.");
  server.await?;
  info!("none", "Core now exiting.");
  Ok(())
}
