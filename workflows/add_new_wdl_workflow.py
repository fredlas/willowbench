import argparse
import json
import lark
import os
import re
import sys
import string
import subprocess

sys.path.append(os.path.abspath('../the_widdler'))
import wdl_grammar
import struct_types_parse

# Writes a JSON file to tell the frontend how to present this workflow, and copies the provided
# WDL file to its appropriate location.
# The output JSON will look like:
# {
#   "name": "Some_WDL-name",
#   "display_group": "Mad Science",
#   "description": "its aliiiiiiive",
#   "inputs": [{"name": "some_file", "type": "File"}, {"name": "an_int", "type": "Int"}]
# }

def _add_mirrored_image_to_csv(csv_filepath, original_path, ecr_path):
    with open(csv_filepath, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([original_path, ecr_path])
    print(f"Successfully mirrored {original_path} to {ecr_path} and updated CSV file.")

# Character order for VALUE mapping: a-z (0-25), A-Z (26-51), 0-9 (52-61)
BASE62_CHARS = string.ascii_lowercase + string.ascii_uppercase + string.digits
CHAR_TO_B62 = {char: index for index, char in enumerate(BASE62_CHARS)} # a->0, ..., z->25, A->26, ..., 9->61
B62_TO_CHAR = list(BASE62_CHARS)
BASE = len(BASE62_CHARS) # 62
def next_filter_string():
    with open('most_recent_filter_string_assigned.txt', 'r') as f:
        s = f.read().strip()
    if len(s) != 4:
        raise ValueError(f"Filter string must be exactly 4 characters long, got '{s}'")
    if s == '9999':
        raise OverflowError("Ran out of 4-character base62 filter strings ('9999' is the last possible)!")
    s_list = list(s)
    carry = 1 # Start by adding 1
    for i in range(3, -1, -1): # Iterate from right to left (index 3 down to 0)
        char = s_list[i]
        if char not in CHAR_TO_B62:
            raise ValueError(f"Invalid character '{char}' in base62 string '{s}'.")
        value = CHAR_TO_B62[char]
        # Add carry and increment
        new_value = value + carry
        # Calculate new digit and next carry
        s_list[i] = B62_TO_CHAR[new_value % BASE]
        carry = new_value // BASE
        # If no more carry and we are not at the leftmost digit, we are done
        if carry == 0 and i > 0:
            break
    ret = "".join(s_list)
    print(f'assigning {ret} and writing to most_recent_filter_string_assigned.txt')
    with open('most_recent_filter_string_assigned.txt', 'w') as f:
        f.write(ret)
    return ret

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

def parse_decl_subtree(decl_children):
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
      assert(decl.data == 'decl')
      decl_name, decl_type, is_optional = parse_decl_subtree(decl.children)
      # TODO do something with optionality - should be reasonable for user inputs to be optional, right?
      processed.append(lark.Tree('pending_var_decl', [lark.Token('DECL_TYPE', decl_type),
                                                      lark.Token('DECL_NAME', decl_name)] ))
    return lark.Tree('input_decls', processed)

  def output_decls(self, children):
    processed = []
    for decl in children:
      assert(decl.data == 'decl')
      decl_name, decl_type, is_optional = parse_decl_subtree(decl.children)
      processed.append(lark.Tree('pending_var_decl', [lark.Token('DECL_TYPE', decl_type),
                                                      lark.Token('DECL_NAME', decl_name)] ))
    return lark.Tree('output_decls', processed)

def check_for_willow_image_name(wdl_filepath):
  try:
    with open(wdl_filepath, 'r', encoding='utf-8') as f:
      file_contents = f.read()
  except FileNotFoundError:
    raise ValueError(f'Specified WDL file path {wdl_filepath} does not exist') from None
  except IOError as e:
    raise ValueError(f"An error occurred while reading {wdl_filepath}: {e}") from None
  if 'willow_image_name' in file_contents:
    print('VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV')
    print('  Looks like you specified a non-default willow_image_name!')
    print('  Be sure to add an entry to the get_ami() mapping in core/src/taskmaster.rs!')
    print('\n oh and did you MAKE THESE NEW IMAGES PUBLIC in AWS?')
    print('^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^')

def parse_declarations_from_wdl_file(wdl_filepath):
  try:
    with open(wdl_filepath, 'r', encoding='utf-8') as f:
      file_contents = f.read()
  except FileNotFoundError:
    raise ValueError(f'Specified WDL file path {wdl_filepath} does not exist') from None
  except IOError as e:
    raise ValueError(f"An error occurred while reading {wdl_filepath}: {e}") from None

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
  def _get_workflow_template(root):
    for child in root.children:
      if type(child) == lark.Tree and child.data == 'workflow':
        return child
  wf_tree = _get_workflow_template(tree)
  if not wf_tree:
      return [], []
  wf_tree = GetWorkflowDeclarationsTF().transform(wf_tree)

  inputs_thing = []
  outputs_thing = []
  for child in wf_tree.children:
    if type(child) == lark.Tree and child.data == 'input_decls':
      for c in child.children:
        to_add = {}
        to_add['type'] = c.children[0].value
        to_add['name'] = c.children[1].value
        inputs_thing.append(to_add)
    elif type(child) == lark.Tree and child.data == 'output_decls':
      for c in child.children:
        to_add = {}
        to_add['type'] = c.children[0].value
        to_add['name'] = c.children[1].value
        outputs_thing.append(to_add)
  return inputs_thing, outputs_thing

def do_gcr_to_ecr_migrations(ssh_path, wdl_filepath):
  # Ensure the new .wdl file(s) are on dev, to enable the next step
  # subprocess.run(f'pushd .. && python3 deploy_fancy.py {ssh_path} --push-only && popd',
  #                shell=True, check=True)
  rsync_cmd = ["rsync", "-avz", "-e", "ssh -i ~/.ssh/adminfredtest.pem",
              "--exclude=.aider*", "--exclude=__pycache__", "--exclude=zzz_notes_and_stuff",
              "--exclude=.git", "--exclude=target", "--exclude=Cargo.lock", "--exclude=.gitignore",
              "--exclude=core/herder_cutoff_map.txt", "--exclude=frontend/willow_backend_port.txt",
              "--out-format=%n", "..", f"{ssh_path}:willow/"]
  print("Running rsync...")
  scp_result = subprocess.run(rsync_cmd, capture_output=True, text=True)
  if scp_result.returncode != 0:
    print("File copy failed!")
    print(scp_result.stderr)
    raise ValueError("File copy failed")
  print('files sent: '+', '.join([line.strip() for line in scp_result.stdout.splitlines() if line.strip()]))

  # Migrate any unmigrated Docker images from GCR to AWS ECR, and record the results
  result = subprocess.run(["ssh", "-i", os.path.expanduser("~/.ssh/adminfredtest.pem"), ssh_path,
                            f'cd willow/workflows && python3 migrate_docker_images.py {wdl_filepath}'],
                          capture_output=True, text=True)
  if result.returncode != 0:
    print(f'ssh dev "python3 migrate_docker_images.py" failed. The stderr:\n\n{result.stderr}')
    sys.exit(1)
  new_csv_lines = result.stdout
  for line in new_csv_lines.splitlines():
    if not line:
      continue
    image_name, ecr_image_uri_with_tag = line.split(',')
    _add_mirrored_image_to_csv('mirrored_gcr_to_ecr_images.csv', image_name, ecr_image_uri_with_tag)

def main():
    if len(sys.argv) != 3:
      print('usage: add_new_wdl_workflow.py workflow_main.wdl admin@ec2-11-22-33-44.compute-1.amazonaws.com')
      sys.exit(1)
    if not sys.argv[1].endswith('.wdl'):
      print('workflow main .wdl filepath expected to end with .wdl')
      sys.exit(1)
    if '/' in sys.argv[1]:
      print('must start with the workflow file(s) youre importing here in workflows/ (TODO remove this constraint)')
      sys.exit(1)
    if not sys.argv[2].startswith('admin@'):
      print('dev machine ssh path expected to start with admin@')
      sys.exit(1)
    wdl_filepath = sys.argv[1]
    ssh_path = sys.argv[2]

    wf_name = os.path.basename(wdl_filepath).replace('.wdl', '')
    if wf_name[0] == '-':
      raise ValueError(f'illegal for workflow name {wf_name} to start with -.')
    if not re.match(r'^[a-zA-Z0-9_-]+$', wf_name):
      raise ValueError(f'Invalid workflow name {wf_name}. Valid chars are: a-z A-Z 0-9 _-')
    if len(wf_name) > 97:
      raise ValueError(f'Workflow name too long; max is 97 characters.')

    if not os.getcwd().endswith("willow/workflows"):
      raise ValueError('Must run from the workflows directory of the willow repo.')

    # TODO seems we can just pull from us.gcr.io (or else this migration wouldn't work, duhhhh!!!!)
    # yeah... removed the migrate_docker_images.py script for now. go find the "remove GCR->ECR mapping"
    # commit if you want to uncomment the following:
    # do_gcr_to_ecr_migrations(ssh_path, wdl_filepath)

    description = input('Enter user-facing workflow description: ')
    inputs, outputs = parse_declarations_from_wdl_file(wdl_filepath)
    json_data = {
      'name': wf_name,
      'filter_string': next_filter_string(),
      'display_group': 'unusedTODOremove',
      'description': description,
      'inputs': inputs,
      'outputs': outputs
    }

    with open(f'{wf_name}.json', 'w') as json_file:
      json.dump(json_data, json_file, indent=4)

    check_for_willow_image_name(wdl_filepath)
    print(f'Workflow {wf_name} successfully imported. Now git commit, and do a full redeploy.')

if __name__ == '__main__':
    main()
