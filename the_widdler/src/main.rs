use actix_web::{web, App, HttpServer, get, post, HttpResponse};
use futures::stream::TryStreamExt;
use mongodb::{bson, Client};
use std::collections::{HashMap, HashSet};
use std::sync::mpsc;
use std::fs;
use std::sync::{Arc, Mutex, MutexGuard};
use std::process::{Command, Stdio, Child};
use std::io::{BufRead, BufReader, Write, stdout, stderr};
use serde_json::Value;
use std::time::Duration;

type WiddlersMap = Arc<HashMap<usize, Arc<Mutex<Child>>>>;

fn basic_job_id_hash(job_id: &str) -> usize
{
    (job_id.chars().nth(16).unwrap() as usize - ' ' as usize) * 3 +
    (job_id.chars().nth(17).unwrap() as usize - ' ' as usize) * 5 +
    (job_id.chars().nth(18).unwrap() as usize - ' ' as usize) * 7
}

fn crash(msg: String)
{
    eprintln!("{}", msg);
    stdout().flush().unwrap(); stderr().flush().unwrap();
    std::process::exit(1);
}

fn json_to_widdler_stdin(message: serde_json::Value, mut guarded_widdler_proc: MutexGuard<'_, Child>) -> bool
{
    let child_stdin = guarded_widdler_proc.stdin.as_mut().unwrap();
    if let Err(e) = writeln!(child_stdin, "{}", message)
    {
        eprintln!("Failed to write to Widdler stdin: {}", e);
        return false;
    }
    return true;
}

#[post("/message_to_widdler")]
async fn message_to_widdler(data: web::Json<Value>, widdlers: web::Data<WiddlersMap>, ongoing_jobs: web::Data<Arc<Mutex<HashSet<String>>>>)
-> HttpResponse
{
    let message = data.into_inner();
    let job_id = message["job_id"].as_str().unwrap();

    if message["the_fn"].as_str() == Some("start_job") {
        ongoing_jobs.lock().unwrap().insert(job_id.to_string());
    }

    let num_widdlers = widdlers.len();
    let instance_idx = basic_job_id_hash(job_id) % num_widdlers;

    let child_arc_mutex = match widdlers.get(&instance_idx)
    {
        Some(child_arc_mutex) => Arc::clone(child_arc_mutex),
        None => { eprintln!("Widdler instance {} does not exist.", instance_idx);
                  return HttpResponse::InternalServerError().body("widdler instance not found"); }
    };

    if !json_to_widdler_stdin(message, child_arc_mutex.lock().unwrap()) {
        return HttpResponse::InternalServerError().body("failed to forward message to widdler"); }
    HttpResponse::Ok().body("ok")
}

fn forward_to_core(message: Value, ongoing_jobs: Arc<Mutex<HashSet<String>>>) -> bool
{
    let job_id = message["job_id"].as_str().unwrap();
    let endpoint = match message["the_fn"].as_str() {
        Some("run_task") => "/widdler/run_task",
        Some("job_failed") => {
            ongoing_jobs.lock().unwrap().remove(job_id);
            "/widdler/job_failed"
        },
        Some("job_done") => {
            ongoing_jobs.lock().unwrap().remove(job_id);
            "/widdler/job_done"
        },
        Some("size_query") => "/widdler/size_query",
        Some("jobs_map_clean_safe_to_shutdown") => { return false; },
        Some("task_cache_store") => "/widdler/task_cache_store",
        _ => { eprintln!("unknown the_fn: {}", message["the_fn"].as_str().unwrap_or("Rust None")); return true; },
    };

    let port_str_res = std::fs::read_to_string("/home/admin/willow/frontend/willow_backend_port.txt");
    if let Err(_e) = port_str_res { eprintln!("failed to read willow_backend_port.txt"); return true; }
    let port_str = port_str_res.unwrap();
    let port_res = port_str.trim().parse::<u16>();
    if let Err(_e) = port_res { eprintln!("bad willow_backend_port.txt: {}", port_str); return true; }
    let port = port_res.unwrap();

    let client = reqwest::blocking::Client::new();
    let url = format!("http://[::1]:{}{}", port, endpoint);

    let mut attempts = 0;
    let max_attempts = 4;
    while attempts < max_attempts
    {
        match client.post(&url).json(&message).send()
        {
            Ok(response) =>
            {
                if response.status().is_success() {
                    break; }
                eprintln!("Failed to forward message to core (attempt {}/{}), status: {}, message JSON: {}",
                          attempts + 1, max_attempts, response.status(), message);
            }
            Err(e) =>
            {
                eprintln!("Failed to forward message to core (attempt {}/{}): {}",
                            attempts + 1, max_attempts, e);
            }
        }

        attempts += 1;
        if attempts < max_attempts
        {
            let delay_ms = 900 * (2u64.pow(attempts - 1));
            let basic_rand = basic_job_id_hash(message["job_id"].as_str().unwrap()) as u64;
            let jitter = basic_rand % (delay_ms / 2);
            let sleep_duration = Duration::from_millis(delay_ms + jitter);
            eprintln!("Retrying in {:?}", sleep_duration);
            std::thread::sleep(sleep_duration);
        }
        else {
            eprintln!("Max retry attempts reached for forwarding message to core."); }
    }
    true
}

fn all_present(arr: &Vec<usize>) -> bool
{
    for i in 0..arr.len()
    {
        let mut found = false;
        for j in 0..arr.len() {
            if arr[j] == i {
                found = true; } }
        if !found {
            return false; }
    }
    return true;
}

fn kickoff_widdlers(num_widdlers: usize, workflows_path: &str, shutdown_tx: mpsc::Sender<usize>, ongoing_jobs: Arc<Mutex<HashSet<String>>>) -> WiddlersMap
{
    let mut widdlers = HashMap::new();
    let (start_tx, start_rx) = mpsc::channel();

    for idx in 0..num_widdlers
    {
        let start_tx_clone = start_tx.clone();
        let shutdown_tx_clone = shutdown_tx.clone();
        let ongoing_jobs_clone = ongoing_jobs.clone();

        let mut child = Command::new("python3")
            .arg("the_widdler.py")
            .arg(workflows_path)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn().unwrap();
        let stdout = child.stdout.take().unwrap();
        std::thread::spawn(move ||
        {
            let reader = BufReader::new(stdout);
            let mut stdout_lines = reader.lines();

            // process first line, expecting "ready"
            if let Some(Ok(line)) = stdout_lines.next()
            {
                if let Ok(message) = serde_json::from_str::<Value>(&line)
                {
                    if message["the_fn"].as_str() == Some("ready") {
                        start_tx_clone.send(idx).expect("Failed to send ready signal"); }
                    else {
                        crash(format!("Widdler first message was not 'ready'. Got: {}", line)); }
                }
                else {
                    crash(format!("Widdler sent non-json first line: {}", line)); }
            }

            // process the rest of widdler stdout
            loop
            {
                if let Some(Ok(line)) = stdout_lines.next()
                {
                    if let Ok(message) = serde_json::from_str::<Value>(&line)
                    {
                        if !forward_to_core(message, ongoing_jobs_clone.clone()) { break; }
                    }
                    else { eprintln!("error parsing widdler line as json. bad line: {}", line); }
                }// TODO error! etc macros, like core
                else { eprintln!("error reading line from widdler"); break; }
            }
            shutdown_tx_clone.send(idx).unwrap();
        });
        widdlers.insert(idx, Arc::new(Mutex::new(child)));
    }

    // Drop the original sender so the receiver loop will finish once all thread clones have been
    // dropped, even if some didn't send a message.
    drop(start_tx);
    // Wait for all widdlers to send their "ready" signal.
    let mut ready_widdlers = Vec::new();
    for _ in 0..num_widdlers
    {
        match start_rx.recv_timeout(std::time::Duration::from_secs(30))
        {
            Ok(x) => { ready_widdlers.push(x); },
            Err(_) => { crash("A widdler failed to start or send the ready signal.".to_string()); }
        }
    }
    if !all_present(&ready_widdlers) {
        crash(format!("not all widdlers ready after 30s; only these are ready: {:#?}", ready_widdlers)); }
    eprintln!("all widdlers reported ready!");

    Arc::new(widdlers)
}

#[get("/summarize_active_jobs")]
async fn summarize_active_jobs(ongoing_jobs: web::Data<Arc<Mutex<HashSet<String>>>>,
                               mongo_client: web::Data<Client>) -> HttpResponse
{
    let jobs: Vec<String> = {
        let locked_jobs = ongoing_jobs.lock().unwrap();
        locked_jobs.iter().cloned().collect()
    };

    let mut html = String::from("<html><head><title>Active Jobs Summary</title><style>
        .collapsible { background-color: #eee; color: #444; cursor: pointer; padding: 18px; width: 100%; border: none; text-align: left; outline: none; font-size: 15px; }
        .active, .collapsible:hover { background-color: #ccc; }
        .content { padding: 0 18px; display: none; overflow: hidden; background-color: #f1f1f1; }
        table { border-collapse: collapse; width: 100%; }
        th, td { text-align: left; padding: 8px; border-bottom: 1px solid #ddd; }
        </style></head><body><h1>Active Jobs</h1>");

    for job_id in jobs {
        html.push_str(&format!("<button type=\"button\" class=\"collapsible\">Job ID: {}</button>", job_id));
        html.push_str("<div class=\"content\"><p>Tasks:</p><table><tr><th>Task Name</th><th>Status</th><th>Start Time</th></tr>");

        let tasks_col = mongo_client.database("willow_database").collection::<bson::Document>("tasks");
        let filter = bson::doc! { "job_id": &job_id };

        match tasks_col.find(filter).await {
            Ok(mut cursor) => {
                while let Ok(Some(doc)) = cursor.try_next().await {
                    let task_name = doc.get_str("task_name").unwrap_or("N/A");
                    let status = doc.get_str("status").unwrap_or("N/A");
                    let start_time_bson = doc.get("start_time");
                    let start_time_html = match start_time_bson {
                        Some(bson::Bson::Int64(t)) => format!("<td class=\"timestamp\">{}</td>", t),
                        Some(bson::Bson::Int32(t)) => format!("<td class=\"timestamp\">{}</td>", t),
                        _ => "<td>N/A</td>".to_string(),
                    };
                    html.push_str(&format!("<tr><td>{}</td><td>{}</td>{}</tr>", task_name, status, start_time_html));
                }
            },
            Err(e) => {
                html.push_str(&format!("<tr><td colspan=\"3\">Error fetching tasks: {}</td></tr>", e));
            }
        }
        html.push_str("</table></div>");
    }

    html.push_str(r#"<script>
        var coll = document.getElementsByClassName("collapsible");
        for (var i = 0; i < coll.length; i++) {
            coll[i].addEventListener("click", function() {
                this.classList.toggle("active");
                var content = this.nextElementSibling;
                if (content.style.display === "block") {
                    content.style.display = "none";
                } else {
                    content.style.display = "block";
                }
            });
        }
        var timestamps = document.getElementsByClassName("timestamp");
        for (var i = 0; i < timestamps.length; i++) {
            var tsCell = timestamps[i];
            var unix_timestamp = parseInt(tsCell.textContent, 10);
            if (!isNaN(unix_timestamp)) {
                var date = new Date(unix_timestamp * 1000);
                tsCell.textContent = date.toLocaleString();
            }
        }
        </script></body></html>"#);

    HttpResponse::Ok().content_type("text/html").body(html)
}

#[post("/oh_boy_lets_shutdown_shutdown")]
async fn oh_boy_lets_shutdown_shutdown(widdlers: web::Data<WiddlersMap>)
-> HttpResponse
{
    eprintln!("Herder received shutdown request, initiating graceful shutdown...");
    let mut all_good = true;
    for widdler_mutex in widdlers.values()
    {
        let guarded_widdler_proc = widdler_mutex.lock().unwrap();
        all_good = all_good && json_to_widdler_stdin(serde_json::json!({"the_fn": "begin_shutdown",
                                                                        "job_id": "NA"}),
                                                     guarded_widdler_proc);
    }
    if all_good { HttpResponse::Ok().body("All Widdlers informed of shutdown request.") }
    else { HttpResponse::InternalServerError().body("Failed to inform one or more Widdlers of shutdown.") }
}

#[get("/health")]
async fn health() -> HttpResponse
{
  HttpResponse::Ok().body("ok")
}

fn delete_lines_with_suffix(suffix_to_delete: u16, filepath: &str) -> std::io::Result<()>
{
    // Read the entire file content into a String
    let content = fs::read_to_string(filepath)?;

    let suffix = suffix_to_delete.to_string();
    let mut modified_lines: Vec<String> = Vec::new();

    // Iterate over lines and filter them
    for line in content.lines() {
        if !line.ends_with(&suffix) {
            modified_lines.push(line.to_string());
        }
    }

    // Join the modified lines back into a single String, separated by newlines
    let new_content = modified_lines.join("\n") + "\n";

    // Overwrite the original file with the new content
    fs::write(filepath, new_content)?;

    Ok(())
}

#[actix_web::main]
async fn main() -> std::io::Result<()>
{
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 4 {
        eprintln!("Usage: {} <num_widdlers> <port> <workflows_path>", args[0]);
        stdout().flush().unwrap(); stderr().flush().unwrap();
        std::process::exit(1);
    }

    let num_widdlers: usize = args[1].parse().expect("Invalid num_widdlers");
    let port: u16 = args[2].parse().expect("Invalid port");
    let workflows_path = &args[3];

    let mongo_client_res = Client::with_uri_str("mongodb://127.0.0.1:27017/?directConnection=true&serverSelectionTimeoutMS=2000&appName=willowcore").await;
    if let Err(ref e) = mongo_client_res {
        crash(format!("failed to connect to mongo: {}", e));
    }
    let mongo_client = mongo_client_res.unwrap();

    let (shutdown_tx, shutdown_rx) = mpsc::channel::<usize>();
    let ongoing_jobs = Arc::new(Mutex::new(HashSet::<String>::new()));
    let the_map = kickoff_widdlers(num_widdlers, workflows_path, shutdown_tx, ongoing_jobs.clone());

    eprintln!("Herder starting with {} Widdlers on port {}", num_widdlers, port);
    let server = HttpServer::new(move || {
        App::new()
            .app_data(web::Data::new(the_map.clone()))
            .app_data(web::Data::new(ongoing_jobs.clone()))
            .app_data(web::Data::new(mongo_client.clone()))
            .service(message_to_widdler)
            .service(summarize_active_jobs)
            .service(oh_boy_lets_shutdown_shutdown)
            .service(health)
    })
    .bind(("localhost", port)).expect("failed to bind port")
    .run();

    std::thread::spawn(move ||
    {
        let mut done_indices: Vec<usize> = Vec::new();
        for x in shutdown_rx
        {
            done_indices.push(x);
            if all_present(&done_indices)
            {
                eprintln!("All threads reported done, stopping server NOW");
                stdout().flush().unwrap(); stderr().flush().unwrap();

                // clean up the config file
                let _ = delete_lines_with_suffix(port, "/home/admin/willow/core/herder_cutoff_map.txt");

                std::process::exit(0);
            }
        }
    });
    if let Err(e) = server.await {
        crash(format!("bad herder Actix server shutdown: {}", e.to_string())); }
    Ok(())
}
