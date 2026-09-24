import base64
import zlib
from lark import Transformer, Tree, Token
import lark
from urllib.parse import urlparse

from tf_common import NUMERIC_TYPES, is_resolved, is_propagatable, CloudLocalFile, token_from_python_prim, token_from_finalized_arr_index, WILLOWNULL, decl_type, decl_name, decl_is_optional, decl_rhs, indexed_array_name
from util import log_error,log_warn,log_info,log_debug # pylint: disable=unused-import
from apply_wdl_builtin_fns import apply_wdl_builtin_fn

DEFAULT_DOCKER_IMAGE = 'alpine'
DEFAULT_WILLOW_IMAGE_NAME = 'default'
DEFAULT_MEMORY = '512 MiB'
DEFAULT_DISK = 'local-disk 5 SSD'
DEFAULT_CPU_STR = '1'

_NEEDS_RESOLUTION = object()

def _binary_op_null_error(op, l, r):
  l_str = l.pretty() if isinstance(l, Tree) else str(l)
  r_str = r.pretty() if isinstance(r, Tree) else str(r)
  raise ValueError(f'null is not allowed in {op} expression:\nleft:\n{l_str}\n\nright:\n{r_str}')

# be sure to run this AFTER ImpliedNameTF
class ResolutionTF(Transformer):
  def __init__(self, unitname, job_context):
    super().__init__(visit_tokens=True)
    self.runtime_config = {}
    self.unitname = unitname
    self.job_context = job_context
    self.task_inputs_non_file = {}
    # Populated when processing input blocks. The files that must be localized to the VM
    # before running this unit's command. Maps from local_filepath_target to cloud_url_source.
    self.files_to_localize = {}
    # Populated when processing output blocks. Files to be gotten from the VM after running this
    # unit's command. Maps from FQVN to VM-local filepath.
    self.files_to_cloudize = {}
    self.optional_cloudizes = set()
    self.stdout_requested = False
    self.file_contents_requests = set()
    self.glob_requests = set()

  def _build_task_request(self, bash_cmds, checked_cloudpaths_to_localize):
    req = {'the_fn': 'run_task', 'job_id': self.job_context.job_id, 'task_name': self.unitname, 'useremail': self.job_context.useremail}
    req['bash_cmds'] = base64.b64encode(zlib.compress(bash_cmds.encode('utf-8'))).decode('utf-8')
    req['willow_image_name'] = self.runtime_config.get('willow_image_name', DEFAULT_WILLOW_IMAGE_NAME)
    req['docker_image'] = self.runtime_config.get('docker', DEFAULT_DOCKER_IMAGE)
    req['memory_requested'] = self.runtime_config.get('memory', DEFAULT_MEMORY)
    req['disk_requested'] = self.runtime_config.get('disks', DEFAULT_DISK)
    req['cpu_requested'] = int(self.runtime_config.get('cpu', DEFAULT_CPU_STR))
    if self.runtime_config.get('use_fake_runner_in_dev', False):
      req['use_fake_runner_in_dev'] = 'True'
    req['files_to_localize'] = checked_cloudpaths_to_localize
    req['files_to_cloudize'] = self.files_to_cloudize
    req['optional_cloudizes'] = list(self.optional_cloudizes)
    log_info(f'task {self.unitname} has OPTIONAL CLOUDIZES: {self.optional_cloudizes}')
    req['task_inputs_non_file'] = self.task_inputs_non_file
    req['stdout_requested'] = self.stdout_requested
    req['file_contents_requests'] = list(self.file_contents_requests)
    req['glob_requests'] = list(self.glob_requests)
    req['s3_bucket'] = self.job_context.s3_bucket
    return req

  # BEGIN basic consts and expressions
  # ======= const stuff ========================================================
  def COMMAND1_FRAGMENT(self, frag): return Token('FINALIZED_STR', frag.value)
  def COMMAND2_FRAGMENT(self, frag): return Token('FINALIZED_STR', frag.value)
  def STRING1_FRAGMENT(self, frag): return Token('FINALIZED_STR', frag.value)
  def STRING2_FRAGMENT(self, frag): return Token('FINALIZED_STR', frag.value)
  def int(self, c): return Token('FINALIZED_INT', int(c[0].value))
  def float(self, c): return Token('FINALIZED_FLOAT', float(c[0].value))
  def boolean_true(self, _c): return Token('FINALIZED_BOOLEAN', True)
  def boolean_false(self, _c): return Token('FINALIZED_BOOLEAN', False)

  # ======= EXPR stuff ========================================================
  def add(self, c):
    if not is_propagatable(c[0]) or not is_propagatable(c[1]): return Tree('add', c)
    l, r = c[0], c[1]
    either_type = l.type + r.type
    if 'STR' in either_type and 'ARRAY' not in either_type:
      l_val = str(l.value) if l.value is not None else ''
      r_val = str(r.value) if r.value is not None else ''
      return Token('FINALIZED_STR', l_val + r_val)
    if l.type == 'WILLOWNULL' or r.type == 'WILLOWNULL':
      _binary_op_null_error('addition', l, r)
    if l.type == r.type and l.type == 'FINALIZED_ARRAY':
      return Token('FINALIZED_ARRAY', l.value + r.value)
    if 'FILEARRAY' in l.type and 'FILEARRAY' in r.type:
      if 'PENDING' in either_type:
        return Token('PENDING_FILEARRAY', l.value + r.value)
      return Token('FINALIZED_FILEARRAY', l.value + r.value)
    if 'ARRAY' in either_type:
      raise ValueError(f'Cannot add types {l.type} and {r.type}')
    if l.type not in NUMERIC_TYPES or r.type not in NUMERIC_TYPES:
      raise ValueError(f'Cannot add types {l.type} and {r.type}')
    ret_type = 'FINALIZED_FLOAT' if 'FLOAT' in (l.type+r.type) else 'FINALIZED_INT'
    return Token(ret_type, l.value + r.value)

  def weird_nulling_add(self, c):
    if not is_propagatable(c[0]) or not is_propagatable(c[1]): return Tree('weird_nulling_add', c)
    l, r = c[0], c[1]
    if l.type == 'WILLOWNULL' or r.type == 'WILLOWNULL':
      return Token('FINALIZED_STR', '')
    return self.add(c)

  def sub(self, c):
    if not is_resolved(c[0]) or not is_resolved(c[1]): return Tree('sub', c)
    l, r = c[0], c[1]
    if l.type == 'WILLOWNULL' or r.type == 'WILLOWNULL':
      _binary_op_null_error('subtraction', l, r)
    if l.type not in NUMERIC_TYPES or r.type not in NUMERIC_TYPES:
      raise ValueError(f'Cannot subtract types {l.type} and {r.type}')
    ret_type = 'FINALIZED_FLOAT' if 'FLOAT' in (l.type+r.type) else 'FINALIZED_INT'
    return Token(ret_type, l.value - r.value)

  def mul(self, c):
    if not is_resolved(c[0]) or not is_resolved(c[1]): return Tree('mul', c)
    l, r = c[0], c[1]
    if l.type == 'WILLOWNULL' or r.type == 'WILLOWNULL':
      _binary_op_null_error('multiplication', l, r)
    if l.type not in NUMERIC_TYPES or r.type not in NUMERIC_TYPES:
      raise ValueError(f'Cannot multiply types {l.type} and {r.type}')
    ret_type = 'FINALIZED_FLOAT' if 'FLOAT' in (l.type+r.type) else 'FINALIZED_INT'
    return Token(ret_type, l.value * r.value)

  def div(self, c):
    if not is_resolved(c[0]) or not is_resolved(c[1]): return Tree('div', c)
    l, r = c[0], c[1]
    if l.type == 'WILLOWNULL' or r.type == 'WILLOWNULL':
      _binary_op_null_error('division', l, r)
    if l.type not in NUMERIC_TYPES or r.type not in NUMERIC_TYPES:
      raise ValueError(f'Cannot divide types {l.type} and {r.type}')
    return Token('FINALIZED_FLOAT', l.value / r.value)

  def rem(self, c):
    if not is_resolved(c[0]) or not is_resolved(c[1]): return Tree('rem', c)
    l, r = c[0], c[1]
    if l.type == 'WILLOWNULL' or r.type == 'WILLOWNULL':
      _binary_op_null_error('modulo', l, r)
    if l.type not in NUMERIC_TYPES or r.type not in NUMERIC_TYPES:
      raise ValueError(f'Cannot modulo types {l.type} and {r.type}')
    ret_type = 'FINALIZED_FLOAT' if 'FLOAT' in (l.type+r.type) else 'FINALIZED_INT'
    return Token(ret_type, l.value % r.value)

  def land(self, c):
    if not is_resolved(c[0]) or not is_resolved(c[1]): return Tree('land', c)
    l, r = c[0], c[1]
    if l.type == 'WILLOWNULL' or r.type == 'WILLOWNULL':
      _binary_op_null_error('logical-AND', l, r)
    if 'FILE' in l.type or 'FILE' in r.type:
      raise ValueError(f'Cannot logical AND {l.type} and {r.type}')
    return Token('FINALIZED_BOOLEAN', bool(l.value) and bool(r.value))

  def lor(self, c):
    if not is_resolved(c[0]) or not is_resolved(c[1]): return Tree('lor', c)
    l, r = c[0], c[1]
    if l.type == 'WILLOWNULL' or r.type == 'WILLOWNULL':
      _binary_op_null_error('logical-OR', l, r)
    if 'FILE' in l.type or 'FILE' in r.type:
      raise ValueError(f'Cannot logical OR {l.type} and {r.type}')
    return Token('FINALIZED_BOOLEAN', bool(l.value) or bool(r.value))

  def negate(self, c):
    if not is_resolved(c[0]): return Tree('negate', c)
    if 'FILE' in c[0].type:
      raise ValueError(f'Cannot logical-NOT type {c[0].type}')
    return Token('FINALIZED_BOOLEAN', not bool(c[0].value))

  def _comp(self, c, op):
    if not is_propagatable(c[0]) or not is_propagatable(c[1]): return Tree(op, c)
    l, r = c[0], c[1]
    if l.type == 'WILLOWNULL' or r.type == 'WILLOWNULL':
      _binary_op_null_error(op, l, r)
    if l.type != r.type and not (l.type in NUMERIC_TYPES and r.type in NUMERIC_TYPES):
      raise ValueError(f'Cannot compare {l.value} (type {l.type}) and {r.value} (type {r.type}) with {op}')
    if 'FILE' in l.type and op not in ('eqeq', 'neq'):
      raise ValueError(f'Cannot compare {l.type} and {r.type} with {op}')

    comps = {'eqeq': (lambda a,b: a==b), 'neq': (lambda a,b: a!=b), 'gt': (lambda a,b: a>b),
             'gte': (lambda a,b: a>=b), 'lt': (lambda a,b: a<b), 'lte': (lambda a,b: a<=b)}
    return Token('FINALIZED_BOOLEAN', comps[op](l.value, r.value))

  def eqeq(self, c): return self._comp(c, 'eqeq')
  def neq(self, c): return self._comp(c, 'neq')
  def gt(self, c): return self._comp(c, 'gt')
  def gte(self, c): return self._comp(c, 'gte')
  def lt(self, c): return self._comp(c, 'lt')
  def lte(self, c): return self._comp(c, 'lte')

  # END basic consts and expressions

  def array(self, c):
    if any(not is_propagatable(x) for x in c):
      return Tree('array', c)
    is_file_array = any('FILE' in x.type for x in c)
    if is_file_array:
      file_list = [x.value for x in c] # some of these might be None (coming from WILLOWNULL)
      any_is_pending = any(f is not None and f.cloud_path is None for f in file_list)
      # TODO not sure if the var_name will be properly set here... it should be an indexed one, right? probably needs to get manually set here? or are we ditching var_name?
      return Token('PENDING_FILEARRAY' if any_is_pending else 'FINALIZED_FILEARRAY', file_list)
    return Token('FINALIZED_ARRAY', [x.value for x in c])

  def at(self, c): # array access
    if not is_propagatable(c[0]) or not is_resolved(c[1]):
      return Tree('at', c)
    if c[0].type == 'WILLOWNULL' or c[1].type == 'WILLOWNULL':
      raise ValueError('null is not allowed in array access')
    if 'ARRAY' not in c[0].type:
      raise ValueError(f'array access needs an array, got {c[0].type}')
    if c[1].type != 'FINALIZED_INT':
      raise ValueError(f'array access needs an int index, got {c[1].type}')
    return token_from_finalized_arr_index(c[0], int(c[1].value))

  def ifthenelse(self, c):
    if not is_resolved(c[0]): return Tree('ifthenelse', c)
    if 'FILE' in c[0].type:
      raise ValueError(f'Cannot use type {c[0].type} as an if-then-else condition')
    return c[1] if bool(c[0].value) else c[2]

  def partially_finalized_str(self, c):
    if any(not is_resolved(x) for x in c):
      return Tree('partially_finalized_str', c)
    return Token('FINALIZED_STR', ''.join(str(s.value) for s in c))

  def string(self, c):
    # chop off the leading and trailing ", which ideally shouldn't have even been parsed into tokens, oh well
    assert c[0].value in ('"', "'")
    assert c[len(c)-1].value in ('"', "'")
    c = c[1:-1]
    return self.partially_finalized_str(c)


  # ======= command, var, meta stuff ========================================================

  def command(self, c):
    if any(not is_resolved(x) for x in c): return Tree('command', c)
    return Token('PENDING_CMD', ''.join(str(s.value) for s in c))

  def PENDING_CMD(self, the_cmd):
    if any(v is _NEEDS_RESOLUTION for v in self.runtime_config.values()):
      return Token('PENDING_CMD', the_cmd.value)

    # TODO trim this check once clear this isn't happening
    seen_cloudpaths = set()
    checked_cloudpaths_to_localize = {}
    for clf in self.files_to_localize.values():
      if not clf.cloud_path:
        log_error(f'A files_to_localize entry ({clf.var_name}) was empty or None!')
      if clf.cloud_path in seen_cloudpaths:
        log_warn(f'Duplicate localization queued up! Offending cloudpath: {clf.cloud_path}', self.job_context.job_id)
      seen_cloudpaths.add(clf.cloud_path)
      checked_cloudpaths_to_localize[clf.var_name] = clf.cloud_path

    req_json = self._build_task_request(the_cmd.value, checked_cloudpaths_to_localize)
    self.job_context.queued_tasks_execs.append(req_json)
    return Token('RUNNING_CMD', 'RUNNING')

  def string_literal(self, node): # TODO this and ESCAPED_STRING might not be handling escapes properly...
    if type(node) is list:
      assert len(node) == 1 and type(node[0]) is Token
      return node[0]
    return Token('FINALIZED_STR', node.value.strip('"'))

  def ESCAPED_STRING(self, node):
    return Token('FINALIZED_STR', node.value.strip('"'))

  def ESCAPED_STRING1(self, node):
    return Token('FINALIZED_STR', node.value.strip("'"))

  def _parse_placeholder_options(self, children):
    true_val = None
    false_val = None
    default_val = ''
    sep_str = None
    val_node = None
    for c in children:
      if type(c) is Tree and c.data == 'placeholder_option':
        if not is_resolved(c.children[1]):
          return None
        the_val = c.children[1].value
        if c.children[0].type == 'SEP':
          sep_str = the_val
        elif c.children[0].type == 'TRUE':
          true_val = the_val
        elif c.children[0].type == 'FALSE':
          false_val = the_val
        elif c.children[0].type == 'DEFAULT':
          default_val = the_val
        else:
          raise ValueError(f'unknown placeholder option {c.children[0].type}')
      else:
        if not is_resolved(c):
          return None
        val_node = c
    return true_val, false_val, default_val, sep_str, val_node

  def cmdvarplaceholder(self, c):
    res = self._parse_placeholder_options(c)
    if res is None:
      return Tree('cmdvarplaceholder', c)
    true_val, false_val, default_val, sep_str, val_node = res
    if sep_str is None:
      sep_str = ' '
    val = default_val if val_node.value is None else val_node.value

    if val_node.type == 'FINALIZED_FILE':
      self.files_to_localize[val.var_name] = val
      cloud_path = val.cloud_path
      if cloud_path.startswith('s3://'):
          _, key = cloud_path.replace('s3://', '').split('/', 1)
          local_path = key
      elif cloud_path.startswith('http'):
          local_path = urlparse(cloud_path).path.lstrip('/')
      else:
          raise ValueError(f"Unsupported cloud path scheme for file localization: {cloud_path}")
      return Token('FINALIZED_STR', local_path)
    if val_node.type == 'FINALIZED_FILEARRAY':
      paths = []
      for clf in val:
        if clf is None:
          continue
        self.files_to_localize[clf.var_name] = clf
        cloud_path = clf.cloud_path
        if cloud_path.startswith('s3://'):
            _, key = cloud_path.replace('s3://', '').split('/', 1)
            paths.append(key)
        elif cloud_path.startswith('http'):
            paths.append(urlparse(cloud_path).path.lstrip('/'))
        else:
            raise ValueError(f"Unsupported cloud path scheme for file localization: {cloud_path}")
      return Token('FINALIZED_STR', sep_str.join(paths))
    if val_node.type == 'FINALIZED_ARRAY':
      return Token('FINALIZED_STR', sep_str.join(map(str, val)))
    if val_node.type == 'FINALIZED_BOOLEAN':
      if val:
        if true_val is not None:
          return Token('FINALIZED_STR', str(true_val))
        return Token('FINALIZED_STR', 'true')
      else:
        if false_val is not None:
          return Token('FINALIZED_STR', str(false_val))
        return Token('FINALIZED_STR', 'false')
    return Token('FINALIZED_STR', str(val))

  def runtime_kv(self, c):
    the_key = c[0].value
    the_val_tok = c[1]
    if not is_resolved(the_val_tok):
      self.runtime_config[the_key] = _NEEDS_RESOLUTION
      return Tree('runtime_kv', c)
    self.runtime_config[the_key] = the_val_tok.value
    return lark.visitors.Discard

  def EXPLICIT_NAME(self, node):
    # TODO HACK this is a fairly expensive way to do this. The problem: if an optional struct is not
    # set, references to its members hang. So here, when we fail to find a FQVN, we check if any
    # prefix (period-delimited segments) is resolved. If it is, we assume it's our struct and we
    # aren't in it.
    full_name = node.value
    resolved_vars = self.job_context.resolved_variables

    found_self = resolved_vars.get(full_name)
    if found_self:
      return found_self

    # Check if any prefix is resolved. Loop through the string, finding the index of the next dot.
    dot_index = -1
    while True:
      dot_index = full_name.find('.', dot_index + 1)
      # If no more dots are found, we've checked all prefixes (or there were none)
      if dot_index == -1:
        break
      # The prefix is the slice from the start up to (but not including) the current dot
      prefix_name = full_name[:dot_index]
      if prefix_name in resolved_vars:
        return WILLOWNULL
    return node

  def _handle_one_string_rhs_file_assignment(self, local_path, varname, originating_fqvn, is_optional):
    self.files_to_cloudize[originating_fqvn] = local_path
    if is_optional:
      self.optional_cloudizes.add(originating_fqvn)
    return CloudLocalFile(varname, originating_fqvn=originating_fqvn)

  # This handles assignments like `File f = other_file` or `File f = "path/to/file"`
  def _handle_file_assignment(self, c, rhs_tok):
    the_decl_name = decl_name(c)
    if rhs_tok.type == 'WILLOWNULL':
      return 'WILLOWNULL', None
    if rhs_tok.type == 'PROPAGATABLE_STRUCT':
      # This is a struct assignment, e.g. `MyStruct s2 = s1`
      # We just copy the value, which is a dict of tokens.
      return rhs_tok.type, rhs_tok.value
    # Case 1: RHS is already a CloudLocalFile object(s) (e.g., from another variable)
    if 'FILE' in rhs_tok.type:
      # Case 1a: it's an array of them.
      if 'ARRAY' in rhs_tok.type:
        our_arrname = the_decl_name
        new_files = []
        for i, existing_file in enumerate(rhs_tok.value):
          new_file = CloudLocalFile(var_name=indexed_array_name(our_arrname, i),
                                    originating_fqvn=existing_file.originating_fqvn,
                                    cloud_path=existing_file.cloud_path)
          new_files.append(new_file)
        return rhs_tok.type, new_files
      # Case 1b: it's just one file.
      else:
        new_file = CloudLocalFile(var_name=the_decl_name,
                                  originating_fqvn=rhs_tok.value.originating_fqvn,
                                  cloud_path=rhs_tok.value.cloud_path)
        return rhs_tok.type, new_file
    # Case 2: RHS is a string literal. Could be a cloud path or a local path.
    if rhs_tok.type == 'FINALIZED_STR':
      path_str = rhs_tok.value.strip()
      decl_fq_name = self.unitname + '.' + the_decl_name
      if path_str.startswith('s3://'):
        clf = CloudLocalFile(var_name=the_decl_name, originating_fqvn=decl_fq_name, cloud_path=path_str)
        return 'FINALIZED_FILE', clf
      return 'PENDING_FILE', self._handle_one_string_rhs_file_assignment(path_str, the_decl_name,
                                                                         decl_fq_name, decl_is_optional(c))
    # Case 3: Array version of Case 2: RHS is an array, which had better be strings (local filepaths)
    if rhs_tok.type == 'FINALIZED_ARRAY':
      assert all(type(x) is str for x in rhs_tok.value)
      val_arr = []
      for i, local_path in enumerate(rhs_tok.value):
        if local_path is None:
          continue
        indexed_array = indexed_array_name(the_decl_name, i)
        indexed_fq_name = f'{self.unitname}.{indexed_array}'
        cloud_local_file = self._handle_one_string_rhs_file_assignment(local_path, indexed_array,
                                                                       indexed_fq_name, decl_is_optional(c))
        val_arr.append(cloud_local_file)
      return 'PENDING_FILEARRAY', val_arr
    raise ValueError(f'Unexpected RHS for File assignment: {rhs_tok.type}')

  def _resolve_struct_from_input(self, struct_fields_dict, name_so_far): # pylint: disable=too-many-statements
    for field_name, field_str_val in struct_fields_dict.items():
      name_thru_field = f'{name_so_far}.{field_name}'
      field_fqvn = f'{self.unitname}.{name_thru_field}'
      field_type = self.job_context.struct_field_types[field_name]
      if field_type == 'Int':
        field_val = int(field_str_val)
      elif field_type == 'Float':
        field_val = float(field_str_val)
      elif field_type == 'String':
        field_val = field_str_val
      elif field_type == 'File':
        maybe_cloudpath = self.job_context.resolved_variables.available_cloudpaths.get(field_fqvn)
        if maybe_cloudpath == '':
          field_val = None
        else:
          field_val = CloudLocalFile(var_name=name_thru_field, originating_fqvn=field_fqvn,
                                     cloud_path=maybe_cloudpath)
      elif field_type == 'Boolean':
        field_val = bool(field_str_val)
      elif field_type == 'ArrayFile':
        field_val = []
        if type(field_str_val) is str:
          log_info(f'YYYYUP FILEARRAY field_str_val came as a str (i know that sounds obvious, but we were expecting it NOT to be a str here, so i guess i need a better name). anyways. its contents: {field_str_val}')
        for i, cloudpath in enumerate(field_str_val): # TODO might come in as a string? like "['cp1', 'cp2']"
          available = cloudpath in self.job_context.resolved_variables.available_cloudpaths
          arr_element_name = indexed_array_name(name_thru_field, i)
          arr_element_fqvn = f'{self.unitname}.{arr_element_name}'
          maybe_cloudpath = self.job_context.resolved_variables.available_cloudpaths.get(arr_element_fqvn)
          if maybe_cloudpath == '':
            field_val.append(None)
          else:
            field_val.append(CloudLocalFile(var_name=arr_element_name, originating_fqvn=arr_element_fqvn,
                                            cloud_path=maybe_cloudpath))
      elif field_type == 'Struct':
        if type(field_str_val) is str:
          log_info(f'YYYYUP field_str_val came as a str (i know that sounds obvious, but we were expecting it NOT to be a str here, so i guess i need a better name). anyways. its contents: {field_str_val}')
        field_val = field_str_val # TODO might come in as a string, like "{'field': '123'}" (the values definitely come in as strings, even if it does come as a dict)
        self._resolve_struct_from_input(field_val, name_thru_field)
      else:
        raise ValueError(f'unknown struct field type {field_type} for field named {field_name}')

      if field_type == 'File':
        if field_val is None:
          token = WILLOWNULL
        else:
          pending_or_final = 'PENDING_FILE' if field_val.cloud_path is None else 'FINALIZED_FILE'
          token = Token(pending_or_final, field_val)
      elif field_type == 'ArrayFile':
        pending_or_final = 'PENDING_FILEARRAY' if any((x is not None and (x.cloud_path is None or x.cloud_path not in self.job_context.resolved_variables.available_cloudpaths)) for x in field_val) else 'FINALIZED_FILEARRAY'
        token = Token(pending_or_final, field_val)
      elif field_type == 'Struct':
        token = Token('PROPAGATABLE_STRUCT', field_val)
      else:
        token = token_from_python_prim(field_val)
      self.job_context.insert_resolved_var(field_fqvn, token)

  def _final_rhs_token_for_decl(self, c):
    rhs_tok = decl_rhs(c)
    if 'File' in decl_type(c):
      final_type, final_value = self._handle_file_assignment(c, rhs_tok)
      return Token(final_type, final_value)
    if rhs_tok.type == 'WILLOWNULL':
      return WILLOWNULL
    if decl_type(c) == 'String' and rhs_tok.type == 'FINALIZED_FILE':
      log_info(f'DOING resolution of decl F-to-S assignment, rhs {rhs_tok}, decl name {decl_name(c)}')
      return Token('FINALIZED_STR', str(rhs_tok.value))
    if rhs_tok.type == 'PROPAGATABLE_STRUCT':
      self._resolve_struct_from_input(rhs_tok.value, decl_name(c))
      return rhs_tok
    if decl_type(c) == 'Struct':
      # This is for struct-to-struct assignment, e.g. `Struct s2 = s1`, so,
      # simply copy the dict (.value) from s1 to s2
      return rhs_tok
    # TODO task_inputs_non_file is for call caching... is it ok if structs are excluded?
    self.task_inputs_non_file[decl_name(c)] = str(rhs_tok.value)
    return rhs_tok

  def var_decl_thing(self, c):
    rhs = decl_rhs(c)
    if not is_propagatable(rhs) or (decl_type(c) == 'String' and rhs.type == 'PENDING_FILE'):
      if is_propagatable(rhs) and decl_type(c) == 'String' and rhs.type == 'PENDING_FILE':
        log_info(f'delaying resolution of decl F-to-S assignment, rhs {rhs}, decl name {decl_name(c)}')
      return Tree('var_decl_thing', c)

    final_rhs_token = self._final_rhs_token_for_decl(c)
    decl_fq_name = self.unitname + '.' + decl_name(c)
    if final_rhs_token.type == 'WILLOWNULL' and not decl_is_optional(c):
      raise ValueError(f'non-optional variable {decl_fq_name} received null/None value')
    self.job_context.insert_resolved_var(decl_fq_name, final_rhs_token)
    return lark.visitors.Discard

  def decl(self, c): # these should not exist at this point
    raise ValueError(f'got decl in ResolutionTF: {c}') from None


# ======= built-in functions (aka "apply") ==========
  def apply_wdl_fn(self, children):
    return apply_wdl_builtin_fn(self, children)
