use std::collections::HashMap;
use serde_json::{json, Value};
use once_cell::sync::Lazy;
use std::process::Command;
use mongodb::bson;

use crate::{info, warn};
use crate::mongo::TaskLogs;
use crate::send_to_herder;
use crate::taskmaster::start_task;
use crate::utils::{crash, running_in_dev};

trait FromValue<'a> { fn from_value(value: &'a Value) -> Option<Self> where Self: Sized; }
impl<'a> FromValue<'a> for &'a str { fn from_value(value: &'a Value) -> Option<Self> { value.as_str() }}
impl FromValue<'_> for u64 { fn from_value(value: &Value) -> Option<Self> { value.as_u64() }}
impl FromValue<'_> for bool { fn from_value(value: &Value) -> Option<Self> { value.as_bool() }}
impl FromValue<'_> for String { fn from_value(value: &Value) -> Option<Self>
{
  value.as_str().map(|s| s.to_string())
}}
impl FromValue<'_> for HashMap<String, String> {
    fn from_value(value: &Value) -> Option<Self> {
        match value {
            Value::Object(map) => Some(
                map.iter()
                    .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                    .collect(),
            ),
            _ => None,
        }
    }
}
impl FromValue<'_> for HashMap<String, Value> {
    fn from_value(value: &Value) -> Option<Self> {
        match value {
            Value::Object(map) => Some(
                map.iter().map(|(k, v)| (k.clone(), v.clone())).collect()
            ),
            _ => None,
        }
    }
}
fn get_value<'a, T>(json: &'a Value, key: &str) -> Option<T> where T: FromValue<'a>,
{
    T::from_value(json.get(key)?)
}

pub static FRONTEND_BASE_URL: Lazy<String> = Lazy::new(||
{
  if !running_in_dev() { return "https://bench.willowbench.bio".to_string(); }



  let output = Command::new("sh")
      .arg("-c")
      .arg("ip -6 addr show scope global | grep inet6 | awk '{print $2}' | sed 's,/.*,,'")
      .output().unwrap();
  let full = std::str::from_utf8(&output.stdout).unwrap().trim_end();
  let lines: Vec<&str> = full.split('\n').collect();
  if lines.len() > 1 {
    warn!("none", "Got too many IPs from `ip -6 addr`. Assuming our IP is the first of: {full}"); }

  let maybe_line0 = lines.get(0);
  if !maybe_line0.is_some() {
    crash("could not get our own IPv6 address! Aborting.".to_string()); }
  let core_ip = maybe_line0.unwrap().to_string();
  format!("http://[{core_ip}]:8008")
});

pub async fn process_widdler_message(message: &serde_json::Value, willow_mongo: &std::sync::Arc<crate::mongo::WillowMongo>) -> Result<(), String>
{
  let the_fn: &str =
    if let Some(s) = get_value(message, "the_fn") { s }
    else {return Err("the_fn must be in message!".to_string());};

  let job_id: String =
    if let Some(s) = get_value(message, "job_id") { s }
    else {return Err("job_id must be in message!".to_string());};

  match the_fn
  {
    "run_task" =>
    {
      // (this is all just validation)
      let task_name: String =
        if let Some(s) = get_value(message, "task_name") { s }
        else {return Err("task_name must be in run_task message!".to_string());};

      let _useremail: String = if let Some(s) = get_value(message, "useremail") { s }
        else {return Err("useremail must be in run_task message!".to_string());};

      let _bash_cmds: String =
        if let Some(s) = get_value(message, "bash_cmds") { s }
        else {return Err("bash_cmds must be in run_task message!".to_string());};

      let _willow_image_name: String =
        if let Some(s) = get_value(message, "willow_image_name") { s }
        else {return Err("willow_image_name must be in run_task message!".to_string());};

      let _docker_image: String =
        if let Some(s) = get_value(message, "docker_image") { s }
        else {return Err("docker_image must be in run_task message!".to_string());};

      let _memory_requested: String =
        if let Some(s) = get_value(message, "memory_requested") { s }
        else {return Err("memory_requested must be in run_task message!".to_string());};

      let _cpu_requested: u64 =
        if let Some(s) = get_value(message, "cpu_requested") { s }
        else {return Err("cpu_requested must be in run_task message!".to_string());};

      let _files_to_localize: HashMap<String, String> =
        if let Some(s) = get_value(message, "files_to_localize") { s }
        else {return Err("run_task message's files_to_localize field missing or malformed".to_string());};

      let _task_inputs_non_file: HashMap<String, String> =
        if let Some(s) = get_value(message, "task_inputs_non_file") { s }
        else {return Err("run_task message's task_inputs_non_file field missing or malformed".to_string());};

      let _files_to_cloudize: HashMap<String, String> =
        if let Some(s) = get_value(message, "files_to_cloudize") { s }
        else {return Err("run_task message's files_to_cloudize field missing or malformed".to_string());};
      // (end validation)

      let mut task_inputs: HashMap<String, String> =
          get_value(message, "task_inputs_non_file").unwrap_or_default();
      let file_inputs: HashMap<String, String> = get_value(message, "files_to_localize").unwrap_or_default();
      task_inputs.extend(file_inputs);

      let cache_key = crate::mongo::make_cache_key(&task_name, &task_inputs);
      let cache_hit = willow_mongo.find_task_by_cache_key(&cache_key).await.map_err(|e| e.to_string())?;

      if let Some(cached_task) = cache_hit
      {
        info!(job_id, "task {} cache hit!", task_name);

        let pickle = cached_task.get_str("pickled_outputs").unwrap();
        let mut msg_json = serde_json::json!({
            "the_fn": "task_cache_hit",
            "job_id": job_id,
            "task_name": task_name,
            "pickled_outputs": pickle,
        });

        if get_value(message, "stdout_requested").unwrap_or(false)
        {
          if let Ok(stdout) = cached_task.get_str("stdout") {
            msg_json["cmd_stdout"] = json!(stdout); }
        }
        if let Some(file_contents_bson) = cached_task.get("file_contents")
        {
          let file_contents_json: serde_json::Value = bson::from_bson(file_contents_bson.clone())
                                                          .map_err(|e| e.to_string())?;
          msg_json["file_contents"] = file_contents_json;
        }
        if let Some(glob_lists_bson) = cached_task.get("glob_result_lists")
        {
          let glob_lists_json: serde_json::Value = bson::from_bson(glob_lists_bson.clone())
                                                       .map_err(|e| e.to_string())?;
          msg_json["glob_result_lists"] = glob_lists_json;
        }

        let update_task_logs_fut = willow_mongo.update_task_logs(
            &job_id, &task_name,
            TaskLogs { stdout: "cached".to_string(), stderr: "cached".to_string() });
        let herder_fut = send_to_herder(msg_json);

        update_task_logs_fut.await.map_err(|e| e.to_string())?;
        herder_fut.await?;
      }
      else
      {
        info!(job_id, "task {} cache miss.", task_name);
        start_task(job_id, task_name, message.clone(), willow_mongo.clone())
        .await?;
      }
    },
    "job_failed" =>
    {
      let errmsg: &str =
        if let Some(s) = get_value(message, "errmessage") { s }
        else { "" };
      warn!(job_id, "job failed because {}", errmsg);

      // TODO credits blocker: deduct credits here if we're here because run_dockerless or
      //                       create_and_run_docker_command returned a bad result.returncode

      let mongo_fut = willow_mongo.update_status(&job_id, "failed", errmsg);
      let herder_fut = send_to_herder(serde_json::json!({"the_fn": "cancel_workflow",
                                                         "job_id": job_id}));

      mongo_fut.await.map_err(|e| e.to_string())?;
      herder_fut.await?;
      // TODO trim
      // if let Err(e) = mongo_fut.await {
      //   return Err(format!("Failed to update status in mongo: {}", e)); }
      // if let Err(e) = herder_fut.await {
      //   return Err(format!("Failed to send cancel_workflow to Herder: {}", e)); }
    },
    "job_done" =>
    {
      let the_outputs: HashMap<String, String> =
        if let Some(s) = get_value(message, "outputs") { s }
        else {return Err("job_done message's outputs field missing or malformed".to_string());};

      // TODO credits blocker: deduct credits here

      info!(job_id, "hooray, job finished!");
      willow_mongo.finish_job(&job_id, the_outputs).await.map_err(|e| e.to_string())?;
      // TODO trim
      // if let Err(e) = willow_mongo.finish_job(&job_id, the_outputs).await {
      //   return Err(format!("Failed to finish job in mongo: {}", e)); }
    },
    "task_cache_store" =>
    {
        let task_name: String =
            if let Some(s) = get_value(message, "task_name") { s }
            else { return Err("task_name must be in task_cache_store message!".to_string()); };

        let pickled_outputs: String = get_value(message, "pickled_outputs").ok_or("missing picked_outputs")?;

        info!(job_id, "CACHING task {}", task_name);
        willow_mongo.update_task_completion(&job_id, &task_name, &pickled_outputs)
            .await.map_err(|e| e.to_string())?;
    },
    _ => { return Err(format!("Unexpected the_fn value: {}", the_fn)); }
  }
  return Ok(());
}
