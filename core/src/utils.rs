use lazy_static::lazy_static;
use std::env;
use std::io::{stderr, stdout, Write};
use hmac::{Hmac, Mac};
use sha2::Sha256;
use hex;
use once_cell::sync::Lazy;

// format: yyyymmdd-hhmmss-rrrrrr-workflowname
// where hhmmss is UTC, rrrrrr is random characters a-zA-Z._-
pub type JobId = String;

pub type TaskName = String;

pub type TaskSecret = String;

lazy_static! { static ref RUNNING_IN_DEV: bool =
{
  let args: Vec<String> = env::args().collect();
  args.iter().any(|arg| arg == "--dev") };
}
pub fn running_in_dev() -> bool { *RUNNING_IN_DEV }

pub fn crash(msg: String)
{
  eprintln!("FATAL: {}", msg);
  stdout().flush().unwrap(); stderr().flush().unwrap();
  std::process::exit(1);
}

static TASK_SECRET_SALT: Lazy<Vec<u8>> = Lazy::new(|| {
  let salt_hex = match std::fs::read_to_string("/home/admin/willow_core_env") {
      Ok(val) => val.trim().to_string(), // .trim() to remove potential newline characters
      Err(e) => { crash(format!("Failed to read file /home/admin/willow_core_env: {}", e));
                  "crash".to_string() }
  };
  let salt_bytes = match hex::decode(&salt_hex) {
      Ok(bytes) => bytes,
      Err(e) => { crash(format!("Failed to decode contents of /home/admin/willow_core_env as hex: {}", e));
                  Vec::<u8>::new() }
  };
  if salt_bytes.len() != 16 {
      crash("Contents of willow_core_env must be a 32-character hex string (16 bytes)".to_string()); }

  crate::info!("none", "Read HMAC salt from /home/admin/willow_core_env");
  salt_bytes
});

pub fn generate_task_secret_hmac(job_id: &JobId, task_name: &TaskName) -> TaskSecret
{
  let mut mac = Hmac::<Sha256>::new_from_slice(&TASK_SECRET_SALT).expect("HMAC can take key of any size");
  let task_id_str = format!("{}{}", job_id, task_name);
  mac.update(task_id_str.as_bytes());
  let result = mac.finalize();
  hex::encode(result.into_bytes())
}

pub fn verify_task_secret_hmac(secret: &TaskSecret, job_id: &JobId, task_name: &TaskName) -> bool
{
  *secret == generate_task_secret_hmac(job_id, task_name)
}

// returns the updated delay
pub fn apply_jitter_to_delay(s1: &str, s2: &str, current_delay_seconds: u32) -> u32
{
  // Calculate a basic entropy source from the strings
  let mut entropy_source: u8 = 0;
  for c in s1.chars() {
    entropy_source = entropy_source.wrapping_add(c as u8); }
  for c in s2.chars() {
    entropy_source = entropy_source.wrapping_add(c as u8); }

  // Calculate the actual jitter fraction (0 to 0.25) based on the normalized entropy
  // This will range from 0.0 to 0.25.
  let jitter_fraction = (entropy_source as f64 / 255.0) * 0.25;

  // Calculate the jitter amount in seconds
  let current_delay_f64 = current_delay_seconds as f64;
  let jitter_amount_f64 = current_delay_f64 * jitter_fraction;

  // Determine if we add or subtract based on the parity of the entropy source
  let jittered_delay_f64 =
    if entropy_source % 2 == 0 { current_delay_f64 + jitter_amount_f64 }
    else { let mut res = current_delay_f64 - jitter_amount_f64; if res < 1.0 { res = 1.0; } res };

  jittered_delay_f64.round() as u32
}

#[macro_export]
macro_rules! error {
  ($job_id:expr, $message:literal $(, $arg:expr)*) => {
      log::error!(target: "core", "[core][E] {} [job_id={}]", format!($message $(, $arg)*), $job_id);
  };
  ($job_id:expr, $message:expr) => {
      log::error!(target: "core", "[core][E] {} [job_id={}]", $message, $job_id);
  };
}

#[macro_export]
macro_rules! warn {
  ($job_id:expr, $message:literal $(, $arg:expr)*) => {
      log::warn!(target: "core", "[core][W] {} [job_id={}]", format!($message $(, $arg)*), $job_id);
  };
  ($job_id:expr, $message:expr) => {
      log::warn!(target: "core", "[core][W] {} [job_id={}]", $message, $job_id);
  };
}

#[macro_export]
macro_rules! info {
  ($job_id:expr, $message:literal $(, $arg:expr)*) => {
      log::info!(target: "core", "[core][I] {} [job_id={}]", format!($message $(, $arg)*), $job_id);
  };
  ($job_id:expr, $message:expr) => {
      log::info!(target: "core", "[core][I] {} [job_id={}]", $message, $job_id);
  };
}

#[macro_export]
macro_rules! debug {
  ($job_id:expr, $message:literal $(, $arg:expr)*) => {
      log::debug!(target: "core", "[core][D] {} [job_id={}]", format!($message $(, $arg)*), $job_id);
  };
  ($job_id:expr, $message:expr) => {
      log::debug!(target: "core", "[core][D] {} [job_id={}]", $message, $job_id);
  };
}

#[macro_export]
macro_rules! trace {
  ($job_id:expr, $message:literal $(, $arg:expr)*) => {
      log::trace!(target: "core", "[core][T] {} [job_id={}]", format!($message $(, $arg)*), $job_id);
  };
  ($job_id:expr, $message:expr) => {
      log::trace!(target: "core", "[core][T] {} [job_id={}]", $message, $job_id);
  };
}
