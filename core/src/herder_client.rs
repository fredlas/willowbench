use once_cell::sync::Lazy;
use serde_json::Value;
use crate::{error};
use crate::utils::crash;

// A static reqwest client is used for performance benefits (e.g., connection pooling).
static CLIENT: Lazy<reqwest::Client> = Lazy::new(reqwest::Client::new);

// herder_cutoff_map.txt format: multiple lines of (yyyymmdd-hhmmss port_num), meaning "no job ID
// with a timestamp later that this cutoff may be directed to port_num". The lines should be sorted
// by ascending cutoff time, and the final line's cutoff should be 99999999-999999. This lets core
// iterate through, and choose the first entry whose cutoff isn't violated.
// e.g.:
//20250609-223344 6666
//20250611-040103 6667
//99999999-999999 6668
static HERDER_CUTOFFS: Lazy<Vec<(String, u16)>> = Lazy::new(||
{
    let content_res = std::fs::read_to_string("/home/admin/willow/core/herder_cutoff_map.txt");
    if let Err(ref e) = content_res {
        crash(format!("error reading /home/admin/willow/core/herder_cutoff_map.txt: {}", e)); }
    let content = content_res.unwrap();

    let mut cutoffs = Vec::<(String, u16)>::new();
    for line in content.lines()
    {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.is_empty() {
            continue; }
        if parts[0].len() != 15 || !parts[0].get(..15).is_some() {
            crash(format!("bad line in core/herder_cutoff_map.txt: {}", line)); }

        // TODO replace with if parts.len() == 2 && let Ok(port) = parts[1].parse::<u16>() {
        // (literally today: https://github.com/rust-lang/rust/issues/142111)
        if parts.len() != 2 {
            crash(format!("bad line in core/herder_cutoff_map.txt: {}", line)); }
        if let Ok(port) = parts[1].parse::<u16>() {
            cutoffs.push((parts[0].to_string(), port)); }
        else {
            crash(format!("bad line in core/herder_cutoff_map.txt: {}", line)); }
    }
    cutoffs
});

fn pick_herder_port(job_id: &str) -> u16
{
    let timestamp = job_id.get(..15).unwrap();

    for (cutoff, port) in HERDER_CUTOFFS.iter() {
        if *timestamp < **cutoff { return *port; }}

    error!(job_id, "No herder port found! Last entry in herder_cutoff_map.txt should be 99999999-999999");
    0
}

// Sends a message to the appropriate Herder instance.
// The Herder is selected based on the timestamp in the job_id.
pub async fn send_to_herder(message: Value) -> Result<(), String>
{
    let port = pick_herder_port(message["job_id"].as_str().ok_or("missing job_id")?);
    let url = format!("http://[::1]:{}/message_to_widdler", port);

    // The static reqwest client sends the request.
    let response = CLIENT.post(&url)
        .json(&message)
        .send()
        .await
        .map_err(|e| format!("Request to herder failed: {}", e))?;

    // Check if the request was successful (status code 2xx).
    if response.status().is_success() {
        Ok(())
    } else {
        Err(format!("Herder returned non-success status: {}", response.status()))
    }
}
