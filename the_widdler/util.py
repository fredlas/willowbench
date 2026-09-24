import datetime as dt
import sys

def log_common(the_msg, lvl, job_id):
  current_time = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
  job_part = '' if job_id == 'none' else f' [job_id={job_id}]'
  sys.stderr.write(f"{current_time} [wdlr][{lvl}] {the_msg}{job_part}\n")
  sys.stderr.flush()

def log_error(err_msg, job_id="none"):
  log_common(err_msg, 'E', job_id)

def log_warn(warn_msg, job_id="none"):
  log_common(warn_msg, 'W', job_id)

def log_info(info_msg, job_id="none"):
  log_common(info_msg, 'I', job_id)

def log_debug(debug_msg, job_id="none"):
  log_common(debug_msg, 'D', job_id)

# a little helper: construct with prefix Foo.Bar.Baz, and then e.g. EXPLICIT_NAME "lol", "Baz.lol",
# "Foo.Bar.Baz.lol" will all become "Foo.Bar.Baz.lol". If no overlap, prepend entire prefix.
# assumes strings are structured in segments separated by .
def splice_prefix(p_raw, s_raw):
  s = s_raw.split('.')
  p = p_raw.split('.')
  for p_start in range(len(p)):
    if len(p) - p_start > len(s):
      continue
    all_eq = True
    for i in range(p_start, len(p)):
      if p[i] != s[i - p_start]:
        all_eq = False
        break
    if all_eq:
      return '.'.join(p[:p_start] + s)
  return '.'.join(p + s)

def wf_repr_diff(wf):
  def get_diff_str(section_name, current_content_str):
      prev_lines = wf.last_repr_parts.get(section_name, "").splitlines()
      current_lines = current_content_str.splitlines()
      diff_lines = [line for line in current_lines if line not in prev_lines]
      wf.last_repr_parts[section_name] = current_content_str
      return '\n'.join(diff_lines)

  sep = '-------------------------------------------------------------------\nnew/changed resolved variables:\n\n'
  resvars_content = '\n'.join(f'{str(resvar)}: {str(val)} (type {val.type})' for resvar, val in wf.job_context.resolved_variables.items())
  resvars_str = get_diff_str('resvars', resvars_content)

  gathers_str = ''
  if wf.gather_decls:
    gathers_content = ''.join(str(g) + '\n\n' for g in wf.gather_decls if g)
    gathers_str = f"{sep}new/changed pending gathers:\n" + get_diff_str('gathers', gathers_content)
  else:
    get_diff_str('gathers', '')

  scatters_str = ''
  if wf.unresolved_scatters:
    scatters_content = ''.join(str(s) + '\n\n' for s in wf.unresolved_scatters if s)
    scatters_str = f"{sep}new/changed pending scatters:\n" + get_diff_str('scatters', scatters_content)
  else:
    get_diff_str('scatters', '')

  tasks_content = ''
  for name, unit in wf.called_tasks.items():
    tasks_content += f'\n{sep}called task call_as name: {name} (new/changed lines)\n\n' + repr(unit)
  tasks_str = get_diff_str('tasks', tasks_content)

  wf_tree_str = get_diff_str('wf_tree', wf.wf_tree.pretty())

  my_own_chunk = f'Workflow: {wf.my_wf_name}, workflow tree (new/changed lines):\n{wf_tree_str}\n{sep}\n{tasks_str}\n\n{sep}{sep}resolved vars:\n{resvars_str}\n{scatters_str}{gathers_str}'

  if not wf.called_wfs:
    return my_own_chunk
  child_chunks = '\n\n\n'.join([repr(wf) for wf in wf.called_wfs.values()])
  return f'{my_own_chunk}\n\n\n{child_chunks}'

def wf_repr_no_diff(wf):
  sep = '-------------------------------------------------------------------\n'
  resvars_str = '\n\n'.join(f'{str(resvar)}: {str(val)} (type {val.type})' for resvar, val in wf.job_context.resolved_variables.items())
  gathers_str = ''
  if wf.gather_decls:
    gathers_str = f'{sep}pending gathers:\n' + ''.join(str(g) + '\n\n' for g in wf.gather_decls if g)
  scatters_str = ''
  if wf.unresolved_scatters:
    scatters_str = f'{sep}pending scatters:\n' + ''.join(str(s) + '\n\n' for s in wf.unresolved_scatters if s)

  tasks_str = ''
  for name, unit in wf.called_tasks.items():
    tasks_str += f'\n{sep}called task call_as name: {name}\n\n' + repr(unit)

  my_own_chunk = f'Workflow: {wf.my_wf_name}, workflow tree:\n{wf.wf_tree.pretty()}\n{sep}\n{tasks_str}\n\n{sep}{sep}resolved vars:\n{resvars_str}\n{scatters_str}{gathers_str}'

  if not wf.called_wfs:
    return my_own_chunk
  child_chunks = '\n\n\n'.join([repr(wf) for wf in wf.called_wfs.values()])
  return f'{my_own_chunk}\n\n\n{child_chunks}'
