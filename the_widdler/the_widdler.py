import sys
import json
import os
import copy
import traceback
import pickle
import base64

from WDLJob import WDLJob
from util import log_error,log_warn,log_info,log_debug # pylint: disable=unused-import

def main():
  if len(sys.argv) < 2:
    log_error('must give widdler a directory path to read workflows from')
    sys.exit(1)
  workflow_dir = sys.argv[1]

  widdler_app = WiddlerApp(workflow_dir)
  widdler_app.run()

class WiddlerApp:
  def __init__(self, workflow_dir):
    self.job_templates = self._load_templates(workflow_dir)
    self.jobs = {} # maps from JobId to active WDLJob
    self.waiting_to_shutdown = False

  def _load_templates(self, workflow_dir):
    log_info('now loading WDL workflows...')
    templates = {}
    for fname in os.listdir(workflow_dir):
      if not fname.endswith('.json'):
        continue
      base_name = fname.replace('.json', '')
      templates[base_name] = WDLJob(workflow_dir, f'{base_name}.wdl')
    log_info(f'the widdler loaded {len(templates)} WDL workflows.')
    return templates

  def run(self):
    self._msg_to_core({'the_fn': 'ready', 'job_id': 'NA'})
    for line in sys.stdin:
      try:
        req_json = json.loads(line.strip())
        job_id = req_json.get('job_id')
        if not req_json.get('the_fn') or not job_id:
          log_error(f'Request missing the_fn or job_id: {line.strip()}')
          continue

        self.handle_request(req_json, job_id)

      except json.JSONDecodeError as e:
        log_error(f'Error parsing JSON: {e}. input line: {line.strip()}')
      except Exception as e:
        err_msg = f'Unexpected error: {e}. input line: {line.strip()}'
        log_error(err_msg)
        traceback.print_exc()
        if 'job_id' in locals() and job_id in self.jobs:
          self.fail_out_job(job_id, err_msg)
      finally:
        if self.waiting_to_shutdown and not self.jobs:
          self._msg_to_core({'the_fn': 'jobs_map_clean_safe_to_shutdown', 'job_id': 'NA'})
          log_info('Widdler jobs dict empty, and we wanted to shut down, so now shutting down.')
          sys.exit(0)

  def _msg_to_core(self, data):
    sys.stdout.write(json.dumps(data) + '\n')
    sys.stdout.flush()

  def fail_out_job(self, job_id, msg, report_to_core=True):
    if job_id in self.jobs:
      if report_to_core:
        self._msg_to_core({'the_fn': 'job_failed', 'job_id': job_id, 'errmsg': msg})
      del self.jobs[job_id]
    else:
      log_warn(f'We were asked to fail non-existent job with error message {msg}', job_id)

  # sends a job_done message (with final outputs) up to core, then removes job_id from the map.
  def clean_up_if_finished(self, job_id):
    job = self.jobs.get(job_id)
    if not job or not (job.all_outputs_available() and not job.any_pending()):
      return
    the_outputs = job.get_root_workflow_outputs()
    for k,v in the_outputs.items():
      if v is not None:
        the_outputs[k] = str(v)
    log_debug(f'will now notify core that job is done, with outputs: {the_outputs}', job_id)
    self._msg_to_core({'the_fn': 'job_done',
                       'job_id': job_id,
                       'outputs': the_outputs })
    del self.jobs[job_id]

  def fire_off_queued_tasks(self, job_id):
    job_ctx = self.jobs[job_id].job_context
    for req_json in job_ctx.queued_tasks_execs:
      task_name = req_json['task_name']
      log_debug(f'about to ask for task {task_name} to be run', job_id)
      self._msg_to_core(req_json)
    job_ctx.queued_tasks_execs.clear()

    dedupd_queries = list({x for sl in job_ctx.queued_size_queries for x in sl})
    if dedupd_queries:
      self._msg_to_core({'the_fn': 'size_query',
                         'job_id': job_id,
                         'cloudpaths': dedupd_queries })
    job_ctx.queued_size_queries.clear()

  def advance_healthy_job(self, job_id):
    self.fire_off_queued_tasks(job_id)
    self.clean_up_if_finished(job_id)

  def make_workflow_instance(self, job_id, wf_name, useremail, s3_bucket, user_inputs):
    ret = copy.deepcopy(self.job_templates[wf_name])
    ret.instantiate(useremail, s3_bucket, job_id, user_inputs)
    return ret

  def handle_task_done(self, job_id, req, job):
    job.notify_command_finished(req)
    if req['status'] != 'OK':
      log_warn(f'task {req["task_call_as"]} reported non-ok status {req["status"]}', job_id)
      self.fail_out_job(job_id, f'Task {req["task_call_as"]} failed with status: {req["status"]}')
    else:
      task_call_as = req['task_call_as']
      outputs = job.get_task_outputs(task_call_as)
      if outputs is not None:
        pickled_outputs = pickle.dumps(outputs)
        b64_encoded_outputs = base64.b64encode(pickled_outputs).decode('ascii')
        log_info(f'asking core to cache task {task_call_as} results!', job_id)
        self._msg_to_core({
            'the_fn': 'task_cache_store',
            'job_id': job_id,
            'task_name': task_call_as,
            'pickled_outputs': b64_encoded_outputs
        })
      self.advance_healthy_job(job_id)


  def handle_request(self, req, job_id):
    fn_name = req['the_fn']
    job = self.jobs.get(job_id)

    try:
      if fn_name == 'begin_shutdown':
        self.waiting_to_shutdown = True
        log_info('now waiting for jobs dict to empty out so we can shut down')
      elif fn_name == 'start_job':
        if job_id in self.jobs:
          log_error('Received start_job request for an existing job_id', job_id)
        else:
          self.jobs[job_id] = self.make_workflow_instance(job_id, req['workflow_name'], req['useremail'],
                                                          req['cloudbucket'], req.get('inputs', {}))
          self.advance_healthy_job(job_id)
      elif job is None:
        log_error(f'Received request {fn_name} for non-existent job', job_id)
      elif fn_name == 'cancel_workflow':
        log_info('asked to cancel job', job_id)
        self.fail_out_job(job_id, 'Workflow cancelled', report_to_core=False)
      elif fn_name == 'files_available':
        job.notify_files_available(req['cloudpaths'])
        self.advance_healthy_job(job_id)
      elif fn_name == 'size_response':
        job.notify_size_response(req['sizes'])
        self.advance_healthy_job(job_id)
      elif fn_name == 'task_cache_hit':
        log_info('task_cache_hit!', job_id)
        job.notify_task_cache_hit(req['task_name'], pickle.loads(base64.b64decode(req['pickled_outputs'])))
        self.advance_healthy_job(job_id)
      elif fn_name == 'task_done':
        self.handle_task_done(job_id, req, job)
      else:
        log_error(f'handle_request(): unknown the_fn value {fn_name}', job_id)
    except Exception as e:
      err_msg = f'Error handling request for job {job_id}: {e}. The full request: {req}'
      log_error(err_msg)
      traceback.print_exc()
      self.fail_out_job(job_id, err_msg)

if __name__ == '__main__':
  main()
