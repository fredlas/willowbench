import re
import os

def _parse_struct(struct_string):
  """
  Parses a simple struct definition to extract field types and names.
  Args:
    struct_string: A string containing the struct definition.
  Returns:
    A list of tuples, where each tuple contains the type and name
    of a field as strings.
  """
  # This regex is the secret sauce. It looks for a type and a name on each line.
  # It's designed to handle simple types, generic types (like Array[Int]), and user-defined types.
  pattern = re.compile(r'\s*([A-Za-z_][A-Za-z0-9_]*(\[.*\])?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*')
  lines = struct_string.strip().split('\n')
  parsed_data = []
  # We pre-define all the "primitive" types.
  # Anything not in this set is considered a "Struct".
  primitive_types = {'Int', 'String', 'File', 'Float', 'Boolean'}
  for line in lines:
    match = pattern.match(line)
    if match:
      # The regex captures the full type string (e.g., "Array[Int]") and the variable name.
      type_str = match.group(1)
      name = match.group(3)
      # This part cleans up the type name for our check.
      # It gets the base type, so "Array[Int]" becomes "Array".
      type_segs = type_str.split('[')
      base_type = type_segs[0]
      if 'Map' in base_type or 'Pair' in base_type:
        raise ValueError('Map and Pair not yet supported as struct fields')
      if 'Array' in base_type and 'File' not in type_segs[1]:
        raise ValueError('the only Array supported as a struct field is Array[File]')
      # Now, we check if the base type is one of our primitives.
      # If it's not, we just call it "Struct".
      if base_type in primitive_types:
        parsed_data.append((type_str, name))
      elif 'Array' in base_type:
        parsed_data.append(('ArrayFile', name))
      else:
        parsed_data.append(('Struct', name))
  return parsed_data

# parses all structs in file_contents, writes field_name->field_type mappings into name_type_dict.
def parse_all_structs(file_contents, name_type_dict):
  # This regex finds all struct blocks and captures their inner content.
  # re.DOTALL is crucial because it allows '.' to match newlines.
  struct_bodies = re.findall(r'struct\s+\w+\s*\{([^}]+)\}', file_contents, re.DOTALL)

  all_results = []
  for body in struct_bodies:
    # Pass the captured content of each struct to our parsing function.
    parsed_fields = _parse_struct(body)
    for field_type, field_name in parsed_fields:
      if field_name in name_type_dict and name_type_dict[field_name] != field_type:
        raise ValueError(f'duplicate field name {field_name} with differing types. sorry, same-name struct fields (yes, as in, in different structs) must have the same type. sorry, i know its dumb.')
      name_type_dict[field_name] = field_type

def get_all_struct_typenames(workflow_dir, root_filename, file_contents_override):
  files_to_scan = [(workflow_dir, root_filename)] # list of (dir, filename)
  scanned_files = set()
  struct_names = set()

  # Handling the test override
  override_abs_path = os.path.normpath(os.path.join(workflow_dir, root_filename))

  while files_to_scan:
    current_dir, filename = files_to_scan.pop(0)
    abs_path = os.path.normpath(os.path.join(current_dir, filename))

    if abs_path in scanned_files:
      continue
    scanned_files.add(abs_path)

    contents = None
    if abs_path == override_abs_path and file_contents_override:
      contents = file_contents_override
    else:
      with open(abs_path, 'r', encoding='utf-8') as f:
        contents = f.read()

    # find structs
    found_structs = re.findall(r'^\s*struct\s+([a-zA-Z][a-zA-Z0-9_]*)\s*\{', contents, re.MULTILINE)
    struct_names.update(found_structs)

    # find imports
    imports = re.findall(r'^\s*import\s+"([^"]+)"', contents, re.MULTILINE)
    for import_path in imports:
      files_to_scan.append((os.path.dirname(abs_path), import_path))

  return list(struct_names)
