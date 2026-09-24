import ast
import json
from lark import Transformer, Tree, Token

from util import log_error,log_warn,log_info,log_debug # pylint: disable=unused-import
from tf_common import CloudLocalFile, my_str_to_bool, WILLOWNULL, decl_meta_unpack, indexed_array_name

from tf_template_time import parse_decl_subtree

# (This is a true template-time one, done when the source .wdl is first read).
# Transform the inputs of the root workflow (which will be provided by the user).
#
# An empty non-optional declaration is fine here; it signals a required user input.
class RootWorkflowInputDeclsTF(Transformer):
  def input_decls(self, children):
    processed = []
    for decl in children:
      assert decl.data == 'decl'
      decl_meta = parse_decl_subtree(decl.children)
      if len(decl.children) > 2:
        processed.append(Tree('pending_root_input_var_decl', [decl_meta, decl.children[2]]))
      else:
        processed.append(Tree('pending_root_input_var_decl', [decl_meta]))
    return Tree('input_decls', processed)

def check_and_parse_array(the_value):
  if type(the_value) is list:
    return the_value
  try:
    stripped = the_value.strip()
    if len(stripped) > 0 and stripped[0] == '[' and stripped[-1] == ']':
      return ast.literal_eval(the_value) if isinstance(the_value, str) else the_value
    else:
      raise ValueError(f'unacceptable ArrayFile input string: {the_value}')
  except Exception as e:
    log_error(f'uh oh, ast.literal_eval failed on:\n{the_value}\n\n exception: {e}')
    raise ValueError('oops') from e

def _parse_decl_input_value(decl_name, decl_type, the_value, workflow_name):
  if decl_type == 'File':
    return Token('PENDING_FILE', CloudLocalFile(var_name=decl_name,
                                                originating_fqvn=f'{workflow_name}.{decl_name}',
                                                cloud_path=None))
  if decl_type == 'ArrayFile':
    the_arr = []
    for i, path in enumerate(check_and_parse_array(the_value)):
      indexed_name = indexed_array_name(decl_name, i)
      the_arr.append(CloudLocalFile(var_name=indexed_name,
                                    originating_fqvn=f'{workflow_name}.{indexed_name}',
                                    cloud_path=None))
    return Token('PENDING_FILEARRAY', the_arr)
  if decl_type == 'Int':
    return Token('FINALIZED_INT', int(the_value))
  if decl_type == 'Float':
    return Token('FINALIZED_FLOAT', float(the_value))
  if decl_type == 'String':
    return Token('FINALIZED_STR', the_value)
  if decl_type == 'Boolean':
    if type(the_value) is str:
      if len(the_value) < 1 or the_value[0] not in {'T', 'F', 't', 'f', 'Y', 'N', 'y', 'n'}:
        raise ValueError(f'unexpected bool str val: {the_value}')
      return Token('FINALIZED_BOOLEAN', my_str_to_bool(the_value))
    if type(the_value) is not bool:
      raise ValueError(f'bool input value type neither bool nor str. type {type(the_value)}, value {the_value}')
    return Token('FINALIZED_BOOLEAN', the_value)
  if decl_type.startswith('Array') and 'File' not in decl_type:
    definite_arr = check_and_parse_array(the_value)
    return Token('FINALIZED_ARRAY', definite_arr)
  if decl_type == 'Struct':
    definite_struct = json.loads(the_value) if isinstance(the_value, str) else the_value
    # ResolutionTF handles mapping these untyped string values to correct types; we just store it.
    return Token('PROPAGATABLE_STRUCT', definite_struct)
  raise ValueError(f'this type is not yet supported for workflow initial input: {decl_type}') from None

class WorkflowUserInputsTF(Transformer):
  def __init__(self, inputs, workflow_name):
    super().__init__(visit_tokens=False)
    self.inputs = inputs
    self.workflow_name = workflow_name

  def _pending_root_input_var_decl(self, d):
    assert d.data == 'pending_root_input_var_decl'
    decl_meta = d.children[0]
    var_type, var_name, var_optional = decl_meta_unpack(decl_meta)
    if var_name not in self.inputs:
      if len(d.children) > 1: # has default rhs
        default_rhs = d.children[1]
        log_info(f'WorkflowUserInputsTF using default value: DECLTYPE {var_type}, name {var_name}, value {default_rhs}')
        return Tree('var_decl_thing', [decl_meta, default_rhs])
      if var_optional:
        log_info(f'WorkflowUserInputsTF filling an optional as empty: DECLTYPE {var_type}, name {var_name}')
        return Tree('var_decl_thing', [decl_meta, WILLOWNULL])
      raise ValueError(f'WorkflowUserInputsTF: workflow {self.workflow_name} not given input for non-optional, no-default input var {var_name}')
    return Tree('var_decl_thing', [decl_meta,
                                   _parse_decl_input_value(var_name, var_type, self.inputs[var_name],
                                                           self.workflow_name)])
  def input_decls(self, children):
    return Tree('input_decls', [self._pending_root_input_var_decl(decl) for decl in children])
