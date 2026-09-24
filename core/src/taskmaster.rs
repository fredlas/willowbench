use std::io::{Error,stdout,stderr,Write};
use std::sync::Arc;
use tokio::time;
use crate::{info, warn, debug};

use crate::send_to_herder;
use crate::mongo::WillowMongo;
use crate::utils::{running_in_dev, TaskName, JobId, generate_task_secret_hmac, apply_jitter_to_delay};
use crate::FRONTEND_BASE_URL;

pub async fn cancel_task(instance_id: String, region: String, job_id: &str, task_name: String)
-> Result<(), String>
{
  let output = tokio::process::Command::new("aws").args(&["ec2", "terminate-instances",
                                                          "--region", &region,
                                                          "--instance-ids", &instance_id]).output();
  match output.await
  {
    Ok(output) =>
    {
        if output.status.success()
        {
            info!(job_id, "Successfully cancelled task {} EC2 VM instance: {} in region {}",
                  task_name, instance_id, region);
            Ok(())
        }
        else
        {
            Err(format!("Failed to terminate instance: {}. Error: {}", instance_id,
                        String::from_utf8_lossy(&output.stderr)))
        }
    }
    Err(e) => { Err(format!("Error running aws ec2 terminate-instances: {}", e)) }
  }
}

// TODO would be nice to not have to redeploy for this
fn get_ami<'a>(willow_image_name: &'a str, aws_region_name: &'a str) -> &'a str
{
  match (willow_image_name, aws_region_name)
  {
    // default: these are all the same image (that I made), debian12 with docker
    ("default", "us-east-1") => "ami-056ae46956a718327",
    ("default", "us-west-1") => "ami-0151d62b8afad33b8",

    ("ExperimentalPipeline", "us-east-1") => "ami-00057069086bde27e",
    ("ExperimentalPipeline", "us-west-1") => "", // TODO

    _ => ""
  }
}

fn determine_memory_scale(mem: &str) -> u64
{
  for c in mem.chars()
  {
    if c == 'm' || c == 'M' { return 1; }
    if c == 'g' || c == 'G' { return 1024; }
    if c == 't' || c == 'T' { return 1024*1024; }
  }
  return 1;
}

struct AwsInstance
{
  name: &'static str,
  memory_mb: u64,
  cpu: u64,
  storage_gb: u64,
  _cost_per_hour_usd: f32,
}
fn determine_instance_type<'a>(mem_req: &'a str, cpu_req: u64, storage_req: &'a str) -> &'a str
{
  let mem_req_parts: Vec<&str> = mem_req.split_whitespace().collect();
  let mem_megabyte_scale = if mem_req_parts.len() != 2 { 1 }
                           else { determine_memory_scale(mem_req_parts[1]) };
  let mem_megabytes_needed = mem_req_parts[0].parse::<u64>().unwrap() * mem_megabyte_scale;

  let disk_req_parts: Vec<&str> = storage_req.split_whitespace().collect();
  let disk_gigabytes_needed = disk_req_parts[1].parse::<u64>().unwrap();
  // TODO parts[2] is either SSD or HDD; would be nice to use that

  // NOTE: IMPORTANT!!! These must be sorted by increasing hourly cost, because our logic scans
  //       through and takes the first acceptable one.
  // TODO some might have slightly more expensive but 2x bandwidth options... might be worth it if
  //      getting Docker images or input files loaded is taking significant time. (e.g. m5ad.large -> m5dn.large)
  // NOTE: "5" storage GB actually means 0, but they have the 8GB root partition we can use ~5 of
  let aws_instance_types = vec![
AwsInstance { name: "t3a.nano", memory_mb: 512, cpu: 2, storage_gb: 5, _cost_per_hour_usd: 0.0047 },
AwsInstance { name: "t3a.micro", memory_mb: 1024, cpu: 2, storage_gb: 5, _cost_per_hour_usd: 0.0094 },
AwsInstance { name: "t3a.small", memory_mb: 2048, cpu: 2, storage_gb: 5, _cost_per_hour_usd: 0.188 },
AwsInstance { name: "t3a.medium", memory_mb: 4096, cpu: 2, storage_gb: 5, _cost_per_hour_usd: 0.0376 },
AwsInstance { name: "t3a.large", memory_mb: 8192, cpu: 2, storage_gb: 5, _cost_per_hour_usd: 0.0752 },
AwsInstance { name: "c5ad.large", memory_mb: 4096, cpu: 2, storage_gb: 75, _cost_per_hour_usd: 0.086 },
AwsInstance { name: "c6id.large", memory_mb: 4096, cpu: 2, storage_gb: 118, _cost_per_hour_usd: 0.1008 },
AwsInstance { name: "m5ad.large", memory_mb: 8192, cpu: 2, storage_gb: 75, _cost_per_hour_usd: 0.103 },
AwsInstance { name: "c3.large", memory_mb: 3840, cpu: 2, storage_gb: 32, _cost_per_hour_usd: 0.105 },
AwsInstance { name: "r5a.large", memory_mb: 16384, cpu: 2, storage_gb: 5, _cost_per_hour_usd: 0.113 },
AwsInstance { name: "m6id.large", memory_mb: 8192, cpu: 2, storage_gb: 118, _cost_per_hour_usd: 0.11865 },
AwsInstance { name: "r5ad.large", memory_mb: 16384, cpu: 2, storage_gb: 75, _cost_per_hour_usd: 0.131 },
AwsInstance { name: "m3.large", memory_mb: 7680, cpu: 2, storage_gb: 32, _cost_per_hour_usd: 0.133 },
AwsInstance { name: "t3a.xlarge", memory_mb: 16384, cpu: 4, storage_gb: 5, _cost_per_hour_usd: 0.1504 },
AwsInstance { name: "r6id.large", memory_mb: 16384, cpu: 2, storage_gb: 118, _cost_per_hour_usd: 0.1512 },
AwsInstance { name: "r3.large", memory_mb: 15360, cpu: 2, storage_gb: 32, _cost_per_hour_usd: 0.166 },
AwsInstance { name: "c5d.xlarge", memory_mb: 8192, cpu: 4, storage_gb: 100, _cost_per_hour_usd: 0.192 },
AwsInstance { name: "c3.xlarge", memory_mb: 7680, cpu: 4, storage_gb: 80, _cost_per_hour_usd: 0.21 },
AwsInstance { name: "r5a.xlarge", memory_mb: 32768, cpu: 4, storage_gb: 5, _cost_per_hour_usd: 0.226 },
AwsInstance { name: "m6id.xlarge", memory_mb: 16384, cpu: 4, storage_gb: 237, _cost_per_hour_usd: 0.2373 },
AwsInstance { name: "r5ad.xlarge", memory_mb: 32768, cpu: 4, storage_gb: 150, _cost_per_hour_usd: 0.262 },
AwsInstance { name: "m3.xlarge", memory_mb: 15360, cpu: 4, storage_gb: 80, _cost_per_hour_usd: 0.266 },
AwsInstance { name: "t3a.2xlarge", memory_mb: 32768, cpu: 8, storage_gb: 5, _cost_per_hour_usd: 0.3008 },
AwsInstance { name: "r6id.xlarge", memory_mb: 32768, cpu: 4, storage_gb: 237, _cost_per_hour_usd: 0.3024 },
AwsInstance { name: "r3.xlarge", memory_mb: 31232, cpu: 4, storage_gb: 80, _cost_per_hour_usd: 0.333 },
AwsInstance { name: "m5ad.2xlarge", memory_mb: 32768, cpu: 8, storage_gb: 300, _cost_per_hour_usd: 0.412 },
AwsInstance { name: "r5a.2xlarge", memory_mb: 65536, cpu: 8, storage_gb: 5, _cost_per_hour_usd: 0.452 },
AwsInstance { name: "m3.2xlarge", memory_mb: 30720, cpu: 8, storage_gb: 160, _cost_per_hour_usd: 0.532 },
AwsInstance { name: "r5d.2xlarge", memory_mb: 65536, cpu: 8, storage_gb: 300, _cost_per_hour_usd: 0.576 },
AwsInstance { name: "c6a.4xlarge", memory_mb: 32768, cpu: 16, storage_gb: 5, _cost_per_hour_usd: 0.612 },
AwsInstance { name: "m5a.4xlarge", memory_mb: 65536, cpu: 16, storage_gb: 5, _cost_per_hour_usd: 0.688 },
AwsInstance { name: "c5ad.4xlarge", memory_mb: 32768, cpu: 16, storage_gb: 600, _cost_per_hour_usd: 0.688 },
AwsInstance { name: "x2iedn.xlarge", memory_mb: 131072, cpu: 4, storage_gb: 118, _cost_per_hour_usd: 0.83363 },
AwsInstance { name: "m5ad.4xlarge", memory_mb: 65536, cpu: 16, storage_gb: 600, _cost_per_hour_usd: 0.824 },
AwsInstance { name: "r5a.4xlarge", memory_mb: 131072, cpu: 16, storage_gb: 5, _cost_per_hour_usd: 0.904 },
AwsInstance { name: "g6.2xlarge", memory_mb: 32768, cpu: 8, storage_gb: 450, _cost_per_hour_usd: 0.9776 },
// AwsInstance { name: "c6a.8xlarge", memory_mb: 65536, cpu: 32, storage_gb: 5, _cost_per_hour_usd: 1.224 },
// AwsInstance { name: "m5a.8xlarge", memory_mb: 131072, cpu: 32, storage_gb: 5, _cost_per_hour_usd: 1.376 },
// AwsInstance { name: "c5.9xlarge", memory_mb: 73728, cpu: 36, storage_gb: 5, _cost_per_hour_usd: 1.53 },
// AwsInstance { name: "x2iedn.2xlarge", memory_mb: 262144, cpu: 8, storage_gb: 237, _cost_per_hour_usd: 1.66725 },
// AwsInstance { name: "r5a.8xlarge", memory_mb: 262144, cpu: 32, storage_gb: 5, _cost_per_hour_usd: 1.808 },
// AwsInstance { name: "c6a.12xlarge", memory_mb: 98304, cpu: 48, storage_gb: 5, _cost_per_hour_usd: 1.836 },
// AwsInstance { name: "m4.10xlarge", memory_mb: 163840, cpu: 40, storage_gb: 5, _cost_per_hour_usd: 2.0 },
// AwsInstance { name: "m6a.12xlarge", memory_mb: 196608, cpu: 48, storage_gb: 5, _cost_per_hour_usd: 2.0736 },
// AwsInstance { name: "r5a.12xlarge", memory_mb: 393216, cpu: 48, storage_gb: 5, _cost_per_hour_usd: 2.712 },
  ];
  for i in aws_instance_types {
    if i.memory_mb >= mem_megabytes_needed && i.cpu >= cpu_req && i.storage_gb >= disk_gigabytes_needed {
      return i.name; }}
  return "no_instance_type_meets_these_reqs";
}

async fn bail_out_starting_task(job_id: &JobId, status_details: &str, willow_mongo: Arc<WillowMongo>)
-> Result<(), String>
{
  let mongo_fut = willow_mongo.update_status(job_id, "failed", status_details);
  let herder_fut = send_to_herder(serde_json::json!({"the_fn": "cancel_workflow", "job_id": job_id}));

  mongo_fut.await.map_err(|e| e.to_string())?;
  herder_fut.await?;
  Ok(())
}

pub async fn start_task(job_id: JobId, task_name: TaskName, json_msg: serde_json::Value,
                        willow_mongo: Arc<WillowMongo>) -> Result<(), String>
{
  let useremail = json_msg["useremail"].as_str().unwrap();
  let cust_res = willow_mongo.get_customer_from_useremail(&useremail)
  .await;
  if let Err(err) = cust_res
  {
    bail_out_starting_task(&job_id, "failed to launch EC2 VM: customer not found", willow_mongo)
    .await?;
    return Err(format!("start_task failed: {}", err));
  }
  let customer = cust_res.unwrap();

  let region = customer.cloud_region;
  let credits_style_billing = customer.billing_type == "credits";

  let willow_image_name = json_msg["willow_image_name"].as_str().unwrap();
  let ami_id = get_ami(willow_image_name, &region);
  if ami_id == ""
  {
    bail_out_starting_task(&job_id, "failed to launch EC2 VM: AMI not found", willow_mongo)
    .await?;
    return Err(format!("No AMI mapping exists for WillowImageName {} region {}",
                       willow_image_name, region));
  }

  let instance_type = determine_instance_type(json_msg["memory_requested"].as_str().unwrap(),
                                              json_msg["cpu_requested"].as_i64().unwrap().try_into().unwrap(),
                                              json_msg["disk_requested"].as_str().unwrap());

  let is_dev_fake_e2e = running_in_dev() && json_msg.get("use_fake_runner_in_dev").is_some();
  debug!(job_id, "task name is {}", task_name);
  stdout().flush().unwrap(); stderr().flush().unwrap();
  let launcher_script = if is_dev_fake_e2e {
    debug!(job_id, "running fake_ec2_launcher.py...");
    stdout().flush().unwrap(); stderr().flush().unwrap();
    "../utils/fake_ec2_launcher.py"
  } else {
    "../utils/ec2_launcher.py"
  };

  let task_config_json_str =
      serde_json::to_string(&json_msg)
      .map_err(|e| Error::new(std::io::ErrorKind::InvalidInput,
                              format!("JSON serialization error: {}", e))).unwrap();

  let task_secret = generate_task_secret_hmac(&job_id, &task_name);
  let docker_name = json_msg["docker_image"].as_str().unwrap();

  // Retry configuration
  const MAX_ATTEMPTS: u32 = 7;
  let mut attempt = 0;
  let mut output;

  loop
  {
    let mut cmd = tokio::process::Command::new("python3");
    cmd.arg(launcher_script)
        .arg("--frontend-base-url").arg(&*FRONTEND_BASE_URL)
        .arg("--task-secret").arg(&task_secret)
        .arg("--task-config-json").arg(&task_config_json_str)
        .arg("--aws-region").arg(&region)
        .arg("--ami-id").arg(ami_id)
        .arg("--docker-name").arg(&docker_name)
        .arg("--instance-type").arg(instance_type)
        .arg(if running_in_dev() {"--dev"} else {"--prod"});
    if credits_style_billing
    {
      cmd.arg("--use-willows-own-vpc");
    }
    else // subscription style billing, using their own AWS
    {
      cmd.arg("--customer-aws-id").arg(&customer.aws_id)
         .arg("--sec-grp").arg(&customer.security_group_id)
         .arg("--subnet").arg(&customer.subnet_id);
    }

    match cmd.output().await
    {
        Ok(out) =>
        {
          output = out;
          let the_stdout = String::from_utf8_lossy(&output.stdout);
          if output.status.success() {
            break; }
          else if the_stdout.contains("You have requested more vCPU capacity than your current vCPU limit")
                  && attempt < MAX_ATTEMPTS - 1
          {
            // TODO notify us and/or the customer that this is happening, even if the retry succeeds
            attempt += 1;
            let base_delay_secs = 100 * (1 << (attempt - 1));
            let jittered_delay = apply_jitter_to_delay(&job_id, &task_name, base_delay_secs);

            info!(job_id, "vCPU capacity limit hit, retrying in {}s (attempt {}/{})",
                  jittered_delay, attempt, MAX_ATTEMPTS);
            time::sleep(time::Duration::from_secs(jittered_delay.into())).await;
            continue;
          }
          else {
            break; }
        }
        Err(e) =>
        {
          bail_out_starting_task(&job_id, "failed while trying to launch EC2 VM", willow_mongo)
          .await?;
          return Err(format!("start_task() failed: failed to run ec2_launcher.py: {}", e));
        }
    }
  }

  let the_stdout = String::from_utf8_lossy(&output.stdout);
  if !output.status.success()
  {
    bail_out_starting_task(&job_id, "failed to launch EC2 VM: AWS API call failed", willow_mongo)
    .await?;
    let the_stderr = String::from_utf8_lossy(&output.stderr);
    return Err(format!(
        "ec2_launcher.py failed to launch task {} in region {} with status: {}\nStdout: {}\nStderr: {}",
        task_name, region, output.status, the_stdout, the_stderr));
  }

  // ec2_launcher.py prints the instance ID to stdout
  let instance_id = the_stdout.trim().to_string();
  info!(job_id, "launched task {} in EC2 VM {} in region {}", task_name, instance_id, region);

  // updating mongo in fake version would overwrite the "completed" status, since the task
  // completes and reports back (which is when the crucial "completed" status is set) by the
  // time we reach here. (whereas the real version just initiates startup on an EC2 VM).
  if is_dev_fake_e2e
  {
    let the_stderr = String::from_utf8_lossy(&output.stderr);
    debug!(job_id, "the fake runner stderr:\n {}", the_stderr);
    stdout().flush().unwrap(); stderr().flush().unwrap();
  }
  else // !is_dev_fake_e2e
  {
    let status = format!("running {}", task_name);
    let fut = willow_mongo.update_status(&job_id, &status, &task_name);

    let mut task_inputs: std::collections::HashMap<String, String> =
        serde_json::from_value(json_msg.get("task_inputs_non_file").cloned().unwrap_or_else(|| serde_json::Value::Object(Default::default())))
        .unwrap_or_default();
    let file_inputs: std::collections::HashMap<String, String> =
        serde_json::from_value(json_msg.get("files_to_localize").cloned().unwrap_or_else(|| serde_json::Value::Object(Default::default())))
        .unwrap_or_default();
    task_inputs.extend(file_inputs);

    let insert_fut = willow_mongo.insert_task(&job_id, &task_name, &region, &instance_id, &task_inputs);
    if let Err(e) = fut.await {
      warn!(job_id, "Failed to update status in mongo: {}", e); }
    if let Err(e) = insert_fut.await {
      warn!(job_id, "Failed to insert task into MongoDB: {}", e); }
  }
  return Ok(());
}
