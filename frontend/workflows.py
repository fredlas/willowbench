import os
import json
import re
import sys
import requests
import lark
from flask import make_response, render_template, request, redirect, url_for

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'the_widdler')))
import wdl_grammar # pylint: disable=import-error,disable=wrong-import-position
import struct_types_parse # pylint: disable=import-error,disable=wrong-import-position

import config
import forms
import our_mongo as mdb
import utils
from utils import log_error,log_warn,log_info,log_debug # pylint: disable=unused-import

def check_no_missing_fields(fe_json, wf_name, json_path):
  if 'display_group' not in fe_json:
    utils.crash(f'The workflow in {json_path} is missing its "display_group" field.')
  if 'description' not in fe_json:
    utils.crash(f'The workflow in {json_path} is missing its "description" field.')
  if 'name' not in fe_json:
    utils.crash(f'The workflow in {json_path} is missing its "name" field.')

  for inpt in fe_json['inputs']:
    if 'name' not in inpt:
      utils.crash(f'{wf_name} has an inputs entry missing its name field.')
    if 'type' not in inpt:
      utils.crash(f'{wf_name} has an inputs entry missing its type field.')

  for output in fe_json['outputs']:
    if 'name' not in output:
      utils.crash(f'{wf_name} has an outputs entry missing its name field.')
    if 'type' not in output:
      utils.crash(f'{wf_name} has an outputs entry missing its type field.')

def check_file_and_workflow_names_match(wf_name, json_path):
  json_fname = json_path.split('/')[-1]
  if wf_name != json_fname.replace('.json', ''):
    utils.crash(f'Workflow JSON file {json_fname} filename does not match name field {wf_name}')
  with open(f'../workflows/{wf_name}.wdl') as f:
    wdl_content = f.read()
  the_pattern = "^workflow Foo {$".replace('Foo', wf_name)
  found_right_workflow = False
  for line in wdl_content.split('\n'):
    if re.search(the_pattern, line) is not None:
      found_right_workflow = True
      break
  if not found_right_workflow:
    utils.crash(f'{json_fname}: corresponding .wdl does not have correct workflow name. Must have exactly "workflow {wf_name} {{" on one line (with that exact spacing)')

def _map_rule_to_wdl_type(tree_rule_name):
  type_map = {
      'type_is_file': 'File',
      'type_is_float': 'Float',
      'type_is_int': 'Int',
      'type_is_bool': 'Boolean',
      'type_is_string': 'String',
      'type_is_struct': 'Struct'
  }
  if tree_rule_name in type_map:
    return type_map[tree_rule_name]
  raise ValueError(f'unknown type {tree_rule_name}')

def _parse_decl_subtree(decl_children):
  assert decl_children[0].data == 'type'
  type_info_tree = decl_children[0].children[0]
  if type_info_tree.data == 'type_noncompound': # includes type_is_struct
    my_type = _map_rule_to_wdl_type(type_info_tree.children[0].data)
  elif type_info_tree.data == 'type_is_array':
    my_type = 'Array'
    my_type += _map_rule_to_wdl_type(decl_children[0].children[1].children[0].data)
  elif type_info_tree.data == 'type_is_matrix':
    my_type = 'ArrayArray'
    inner_type_str = _map_rule_to_wdl_type(decl_children[0].children[1].children[0].data)
    if inner_type_str == 'File':
      raise ValueError('Array of Array of Files is not supported.')
    my_type += inner_type_str
  elif type_info_tree.data == 'type_is_map':
    my_type = 'Map'
    my_type += _map_rule_to_wdl_type(decl_children[0].children[1].children[0].data)
    my_type += _map_rule_to_wdl_type(decl_children[0].children[2].children[0].data)
  elif type_info_tree.data == 'type_is_pair':
    my_type = 'Pair'
    my_type += _map_rule_to_wdl_type(decl_children[0].children[1].children[0].data)
    my_type += _map_rule_to_wdl_type(decl_children[0].children[2].children[0].data)
  else:
      raise ValueError(f'Unsupported declaration type: {type_info_tree.data}')

  is_optional = any(isinstance(node, lark.Tree) and node.data == 'optional' for node in decl_children[0].children)
  decl_name = decl_children[1].value
  return decl_name, my_type, is_optional

class GetWorkflowDeclarationsTF(lark.Transformer):
  def input_decls(self, children):
    processed = []
    for decl in children:
      assert decl.data == 'decl'
      decl_name, decl_type, is_optional = _parse_decl_subtree(decl.children)
      our_rhs = None
      if len(decl.children) > 2:
        our_rhs = decl.children[2]
      processed.append(lark.Token('pending_var_decl', (decl_type, decl_name, is_optional, our_rhs) ))
    return lark.Tree('input_decls', processed)

  def output_decls(self, children):
    processed = []
    for decl in children:
      assert decl.data == 'decl'
      decl_name, decl_type, _is_optional = _parse_decl_subtree(decl.children)
      processed.append(lark.Tree('pending_var_decl', [lark.Token('DECL_TYPE', decl_type),
                                                      lark.Token('DECL_NAME', decl_name)] ))
    return lark.Tree('output_decls', processed)

def _parse_one_input_decl(c):
  to_add = {}
  decl_type, decl_name, is_optional, our_rhs = c.value
  to_add['type'] = decl_type
  to_add['name'] = decl_name
  to_add['optional'] = is_optional
  if decl_type == 'Boolean' and is_optional:
    raise ValueError('optional Boolean inputs are forbidden in top-level workflows (doesnt make sense for the UI)')
  if our_rhs is None:
    return to_add

  if our_rhs.data == 'boolean_false':
    to_add['default'] = False
  elif our_rhs.data == 'boolean_true':
    to_add['default'] = True
  elif our_rhs.data == 'string':
    assert our_rhs.children[0].value == '"' and our_rhs.children[2].value == '"'
    to_add['default'] = our_rhs.children[1].value
  elif our_rhs.data in ('fq_name', 'left_name'):
    # minor HACK: apparently it's legal for a workflow to have a task output as the RHS default
    # value of a (non-optional) workflow input. That doesn't fit with our "parse the RHS values and
    # set the HTML edit fields to them" approach here. So, just pretend it actually was optional.
    # The widdler can use the default value, so it won't complain about not receiving a value.
    to_add['optional'] = True
  elif isinstance(our_rhs.children[0], lark.Token):
    to_add['default'] = our_rhs.children[0].value
  else:
    log_warn(f'input decl {decl_name} with RHS {our_rhs.pretty()} not covered by any if-case in _parse_one_input_decl(), which are supposed to be exhaustive. not using this RHS.')
  return to_add

def parse_wdl_for_declarations(wdl_filepath, workflow_name):
  try:
    with open(wdl_filepath, 'r', encoding='utf-8') as f:
      file_contents = f.read()
  except FileNotFoundError:
    utils.crash(f'Specified WDL file path {wdl_filepath} does not exist')
  except IOError as e:
    utils.crash(f"An error occurred while reading {wdl_filepath}: {e}")

  # a bit of a hack to deal with structs, which for some reason Lark has trouble with.
  struct_names = struct_types_parse.get_all_struct_typenames(os.path.dirname(wdl_filepath), os.path.basename(wdl_filepath), file_contents)
  grammar = wdl_grammar.productions
  if struct_names:
    struct_rule = 'type_is_struct: ' + ' | '.join(f'"{s}"' for s in struct_names)
    grammar = grammar.replace('type_is_struct: "WillowStructInstantiationZYXABC"', struct_rule)
  else:
    grammar = grammar.replace(' | type_is_struct', '')
    grammar = grammar.replace('type_is_struct: "WillowStructInstantiationZYXABC"', '')
  file_contents = file_contents.replace('Array[Array[String]]', 'Matrix[String]') # TODO TODO TODO TODO HACK LOLL
  file_contents = file_contents.replace('Array[Array[Int]]', 'Matrix[Int]')
  file_contents = file_contents.replace('Array[Array[Float]]', 'Matrix[Float]')
  file_contents = file_contents.replace('Array[Array[Boolean]]', 'Matrix[Boolean]')
  if 'Array[Array' in file_contents:
    raise ValueError('only Array[Array[{String,Int,Float,Boolean}]] are currently supported')

  parser = lark.Lark(grammar, parser='lalr')
  tree = parser.parse(file_contents)

  def _get_workflow_template(root, wf_name):
    for child in root.children:
      if isinstance(child, lark.Tree) and child.data == 'workflow':
        if child.children[0].value == wf_name:
          return child
    utils.crash(f"Could not find workflow '{wf_name}' in {wdl_filepath}")
    return None

  wf_tree = GetWorkflowDeclarationsTF().transform(_get_workflow_template(tree, workflow_name))

  inputs_list = []
  outputs_list = []
  for child in wf_tree.children:
    if isinstance(child, lark.Tree) and child.data == 'input_decls':
      for c in child.children:
        inputs_list.append(_parse_one_input_decl(c))
    elif isinstance(child, lark.Tree) and child.data == 'output_decls':
      for c in child.children:
        to_add = {}
        to_add['type'] = c.children[0].value
        to_add['name'] = c.children[1].value
        outputs_list.append(to_add)
  return inputs_list, outputs_list

def read_and_validate_wdl(json_path):
  with open(json_path) as f:
    fe_json = json.load(f)

  wf_name = fe_json['name']
  check_file_and_workflow_names_match(wf_name, json_path)

  wdl_path = os.path.join('../workflows', f'{wf_name}.wdl')
  inputs, outputs = parse_wdl_for_declarations(wdl_path, wf_name)
  fe_json['inputs'] = inputs
  fe_json['outputs'] = outputs
  check_no_missing_fields(fe_json, wf_name, json_path)

  if wf_name[0] == '-':
    utils.crash(f'illegal for workflow name to start with -. offender: {wf_name}')
  if not re.match(r'^[a-zA-Z0-9_-]+$', wf_name):
    utils.crash(f'Invalid workflow name {wf_name}. Valid chars are: a-z A-Z 0-9 _-')
  for inpt in fe_json['inputs']:
    varname = inpt['name']
    vartype = inpt['type']
    if varname[0] == '-':
      utils.crash(f'illegal for input variable to start with -. offender: {varname}')
    if not re.match(r'^[a-zA-Z0-9_-]+$', varname):
      utils.crash(f'Invalid input variable name {varname}. Valid chars are: a-z A-Z 0-9 _-')
    if vartype not in ['Int', 'Float', 'String', 'File', 'Boolean', 'Struct', 'ArrayInt', 'ArrayFloat', 'ArrayString', 'ArrayFile', 'ArrayBoolean']:
      utils.crash(f'Input {varname} has invalid type {vartype}. Valid types are Int,Float,String,File,Boolean,Struct.')
  for output in fe_json['outputs']:
    varname = output['name']
    vartype = output['type']
    if varname[0] == '-':
      utils.crash(f'illegal for output variable to start with -. offender: {varname}')
    if not re.match(r'^[a-zA-Z0-9_-]+$', varname):
      utils.crash(f'Invalid output variable name {varname}. Valid chars are: a-z A-Z 0-9 _-')
    if vartype not in ['Int', 'Float', 'String', 'File', 'Boolean', 'Struct', 'ArrayInt', 'ArrayFloat', 'ArrayString', 'ArrayFile', 'ArrayBoolean']:
      utils.crash(f'Output {varname} has invalid type {vartype}. Valid types are Int,Float,String,File,Boolean,Struct.')
  return fe_json

workflows = []
log_info('loading workflows:')
all_wf_names = ''
for workflow_fname in os.listdir('../workflows'):
  if not workflow_fname.endswith('.json'):
    continue
  json_path = os.path.join('../workflows', workflow_fname)
  fe_json = read_and_validate_wdl(json_path)
  workflows.append(fe_json)
  all_wf_names += workflow_fname.replace('.json', '') + ', '
log_info(f'finished loading {len(workflows)} workflows: {all_wf_names}')

grouped_workflows = {}
named_workflows = {}
filter_to_wf_map = {}
wf_to_filter_map = {}
for workflow in workflows:
  grouped_workflows.setdefault(workflow['display_group'], []).append(workflow)
  if workflow['name'] in named_workflows:
    utils.crash(f'Duplicate workflow name: {workflow["name"]}. I suggest you go grep willow/workflows/.')
  named_workflows[workflow['name']] = workflow
  filter_str = workflow['filter_string']
  if filter_str in filter_to_wf_map:
    utils.crash(f'overwriting a filter_to_wf_map entry: {filter_str}')
  filter_to_wf_map[filter_str] = workflow['name']
  wf_to_filter_map[workflow['name']] = filter_str

def set_user_workflow_filter_list(useremail, the_list):
  filter_str = ''
  for s in the_list:
    filter_str += wf_to_filter_map[s]
  mdb.set_user_library_filter(useremail, filter_str)

def get_user_workflow_filter_list(useremail):
  user = mdb.find_user(useremail)
  if not user:
    return None
  if 'library_filter' not in user:
    return None
  the_filter = user['library_filter']
  if not the_filter:
    return None
  ret = []
  for i in range(0, len(the_filter), 4):
    cur_slice = the_filter[i:i+4]
    if cur_slice in filter_to_wf_map:
      ret.append(filter_to_wf_map[cur_slice])
  return ret

def workflows_library_handler(useremail):
  user_filter_list = get_user_workflow_filter_list(useremail)
  # no/empty filter => show all workflows
  if not user_filter_list:
    return render_template('workflows.html', grouped_workflows=grouped_workflows)

  filtered_grouped_workflows = {}
  for displaygroup, workflows in grouped_workflows.items():
    filtered_workflows = [wf for wf in workflows if wf['name'] in user_filter_list]
    if filtered_workflows: # Only add category if there are workflows in it
      filtered_grouped_workflows[displaygroup] = filtered_workflows
  return render_template('workflows.html', grouped_workflows=filtered_grouped_workflows)

def library_edit_handler(useremail):
  form = forms.JustCSRFForm()
  if request.method == 'GET':
    cur_filter_list = get_user_workflow_filter_list(useremail)
    if not cur_filter_list:
      cur_filter_list = list(named_workflows.keys())
    # Pass all workflows AND the current filter to the template
    return render_template('edit_workflows_filter.html',
                           grouped_workflows=grouped_workflows, current_filter=cur_filter_list, form=form)
  if form.validate_on_submit():
    set_user_workflow_filter_list(useremail, request.form.getlist('selected_workflows'))
  return redirect(url_for('workflows_menu_handler'))

def workflows_url_handler(workflow_name, useremail):
  if workflow_name not in named_workflows:
    return (render_template('workflow_not_found.html', workflow_name=workflow_name), 404, {})

  workflow_json = named_workflows[workflow_name]
  DynamicForm = forms.generate_form_class(workflow_json['inputs'])
  form = DynamicForm()

  if form.validate_on_submit():
    input_decls = workflow_json['inputs']
    error_resp, submitted_data = _get_inputs_from_form_submission(input_decls)
    if error_resp:
      return error_resp
    return _validate_and_submit_workflow(input_decls, workflow_name, useremail, submitted_data)

  response = make_response(render_template('workflow.html', form=form, workflow_name=workflow_name))
  response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
  response.headers['Pragma'] = 'no-cache'
  response.headers['Expires'] = '0'
  return response

def is_integer_string(s):
  try:
    _ = int(s)
    return True
  except ValueError:
    return False

def is_float_string(s):
  try:
    _ = float(s)
    return True
  except ValueError:
    return False

def validate_array(s):
  stripped = s.strip()
  return len(stripped) > 0 and stripped[0] == '[' and stripped[-1] == ']'

def _get_inputs_from_api_submission(input_decls):
  if not request.is_json:
    return make_response("Request must be JSON", 415), None
  source_data = request.get_json()

  submitted_data = {}
  defined_input_names = {inpt['name'] for inpt in input_decls}
  for key in source_data:
    if key not in defined_input_names:
      return make_response(f"Error: unknown input parameter '{key}'", 400), None

  for inpt in input_decls:
    cur_name = inpt['name']
    if cur_name in source_data:
      val = source_data[cur_name]
      if val is None:
        submitted_data[cur_name] = ''
      elif isinstance(val, bool):
        submitted_data[cur_name] = str(val).lower()
      else:
        submitted_data[cur_name] = str(val)
    else:
      if inpt['type'] == 'Boolean':
        submitted_data[cur_name] = 'false'
      else:
        submitted_data[cur_name] = ''

  return None, submitted_data

def _get_inputs_from_form_submission(input_decls):
  submitted_data = {}
  source_data = request.form
  for inpt in input_decls:
    cur_name = inpt['name']
    cur_type = inpt['type']
    cur_val = source_data.get(cur_name, '')
    submitted_data[cur_name] = 'false' if cur_type == 'Boolean' and cur_val == '' else cur_val
  return None, submitted_data

def _validate_and_submit_workflow(inputs, workflow_name, useremail, submitted_data):
  cur_job_id = utils.generate_job_id(workflow_name)
  input_values = {}
  input_types = {}
  for inpt in inputs:
    cur_name = inpt['name']
    cur_type = inpt['type']
    cur_val = submitted_data.get(cur_name, '')

    value_empty = not cur_val
    is_optional = inpt.get('optional', False)
    input_types[cur_name] = cur_type
    input_values[cur_name] = cur_val

    if value_empty:
      if not is_optional:
        return make_response(f"Error: non-optional input {cur_name} needs a value", 400)
      del input_types[cur_name]
      del input_values[cur_name]
      continue
    if cur_type == 'Int' and not is_integer_string(cur_val):
      return make_response(f"Error: value {cur_val} for input {cur_name} is not an integer", 400)
    elif cur_type == 'Float' and not is_float_string(cur_val):
      return make_response(f"Error: value {cur_val} for input {cur_name} is not a float", 400)
    elif 'Array' in cur_type and not validate_array(cur_val):
      return make_response(f"Error: value {cur_val} for input {cur_name} is not an array", 400)

  user = mdb.find_user(useremail)
  customer_id = user['customer_id']

  inputs_json = {'job_id': cur_job_id, 'workflow_name': workflow_name, 'customer_id': int(customer_id), 'owning_useremail': useremail, 'inputs': input_values, 'input_types': input_types}
  dumped = json.dumps(inputs_json)
  log_info(f'will do /run_willow_workflow for customer {customer_id} workflow {workflow_name}', cur_job_id)
  log_debug('run_willow_workflow JSON payload: ' + dumped, cur_job_id)
  headers = { 'Content-Type': 'application/json' }
  try:
    backend_port = config.get_backend_port()
    response = requests.post(f"http://[::1]:{backend_port}/run_willow_workflow",
                             data=dumped, headers=headers, timeout=5)
    response.raise_for_status()
    return make_response(json.dumps({'job_id': cur_job_id}), 200, {'Content-Type': 'application/json'})
  except requests.exceptions.RequestException as e:
    error_message = "Job submission failed."
    if hasattr(e, 'response') and e.response is not None:
      try:
        error_body = e.response.text
        error_message = f"Job submission failed: {error_body}"
      except Exception as ex:
        log_warn(f"Error accessing response body: {ex}", cur_job_id)
    log_error(f"Job submission failed: {error_message}", cur_job_id)
    return make_response(error_message, 400)

def handle_api_workflow_submission(workflow_name, admin_email):
  if workflow_name not in named_workflows:
    return make_response(f"Workflow {workflow_name} not found", 404)

  workflow_json = named_workflows[workflow_name]
  input_decls = workflow_json['inputs']

  error_resp, submitted_data = _get_inputs_from_api_submission(input_decls)
  if error_resp:
    return error_resp
  return _validate_and_submit_workflow(input_decls, workflow_name, admin_email, submitted_data)
