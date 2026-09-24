from lark import Transformer, Tree, Token

from tf_common import WILLOWNULL, decl_meta_name, decl_meta_is_optional
from util import log_error,log_warn,log_info,log_debug # pylint: disable=unused-import

# This file is for transformations used in the process of building called tasks from task templates.

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

  is_optional = any(isinstance(node, Tree) and node.data == 'optional' for node in decl_children[0].children)
  decl_name = decl_children[1].value
  return Tree('decl_meta', [Token('DECL_TYPE', my_type),
                            Token('DECL_NAME', decl_name),
                            Token('OPTIONALITY', is_optional)])

def determine_input_decl_rhs(orig_decl, call_inputs, decl_meta):
  # 1) if input is specified in the call_inputs: copy that call_input's RHS (EXPLICIT_NAME or value)
  relevant_call_input = call_inputs.get(decl_meta_name(decl_meta))
  if relevant_call_input:
    return relevant_call_input

  # 2) if the input decl has a RHS default value, copy that value
  if len(orig_decl.children) > 2:
    return orig_decl.children[2] # (indices 0,1 are type,name)

  # 3) if is_optional, make our RHS a WILLOWNULL
  if decl_meta_is_optional(decl_meta):
    return WILLOWNULL

  # 4) none of the above: error out
  raise ValueError(f'non-optional input {decl_meta_name(decl_meta)} receives no value; this WDL file is unrunnable')


# special transformer for the input_decls, so that we can copy the workflow's call blocks' inputs
# sections into the called tasks/non-root workflows.
class NonRootInputDeclsTF(Transformer):
  def __init__(self, call_inputs, call_as_prefix=None):
    super().__init__(visit_tokens=False)
    self.call_as_prefix = call_as_prefix
    self._call_inputs = {} # maps from string decl name to RHS node
    if call_inputs is not None:
      for call_input in call_inputs.children:
        self._call_inputs[call_input.children[0].value] = call_input.children[1]

  def input_decls(self, children):
    log_info(f'{self.call_as_prefix}: call inputs are: {self._call_inputs}')
    processed = []
    for decl in children:
      if decl.data != 'decl':
        raise ValueError(f'unexpected inputs_decls child! data is {decl.data}, whole thing is {decl}')
      decl_meta = parse_decl_subtree(decl.children)
      processed.append(Tree('var_decl_thing',
                            [decl_meta, determine_input_decl_rhs(decl, self._call_inputs, decl_meta)]))
    return Tree('input_decls', processed)

# be sure to run this AFTER FullyQualifiedNameTF! this gets the rest of the var names transformed
# into EXPLICIT_NAMEs, but since it directly works on bare CNAMEs, it would mess up the
# child-of-left_name CNAMEs that FullyQualifiedNameTF wants to handle itself.
class ImpliedNameTF(Transformer):
  def __init__(self, unitname):
    super().__init__(visit_tokens=False)
    self.unitname = unitname

  def left_name(self, children):
    assert children[0].type == 'CNAME'
    return Token('EXPLICIT_NAME', f'{self.unitname}.{children[0].value}')

class TaskOutputDeclsTF(Transformer):
  def __init__(self, collect_output_names, collect_optional_names):
    super().__init__(visit_tokens=False)
    self._collect_output_names = collect_output_names
    self._collect_optional_names = collect_optional_names

  def output_decls(self, children):
    processed = []
    for decl in children:
      assert decl.data == 'decl'
      if len(decl.children) != 3:
        raise ValueError(f'output_decls decl not of the form "Type name = value", parse tree: {decl}') from None
      decl_meta = parse_decl_subtree(decl.children) # uses children[0,1]
      self._collect_output_names.append(decl_meta_name(decl_meta))
      if decl_meta_is_optional(decl_meta):
        self._collect_optional_names.add(decl_meta_name(decl_meta))
      bound_rhs = decl.children[2]
      processed.append(Tree('var_decl_thing', [decl_meta, bound_rhs]))
    return Tree('output_decls', processed)

# be sure to run this AFTER all other DeclsTFs!
class AllOtherDeclsTF(Transformer):
  def decl(self, children):
    decl_meta = parse_decl_subtree(children) # uses children[0,1]
    bound_rhs = None
    if len(children) > 2:
      bound_rhs = children[2]
    elif decl_meta_is_optional(decl_meta):
      bound_rhs = WILLOWNULL
    else:
      raise ValueError(f'non-optional decl {decl_meta_name(decl_meta)} receives no value; this WDL file might be unrunnable')

    return Tree('var_decl_thing', [decl_meta, bound_rhs])

# be sure to run this AFTER ImpliedNameTF! this looks for anything that should have the
# workflow name prepended but doesn't. which happens when the .wdl file had a Foo.var specified by
# task Bar, referring to task Foo. Or, given a struct Foo foo, a use of its field foo.f.
# (should be idempotent, by the way)
class WorkflowPrefixGuarantorTF(Transformer):
  def __init__(self, wf_name, called_task_names_raw, local_structvar_names):
    super().__init__(visit_tokens=True)
    self.wf_name = wf_name
    self.wf_prefix_needers = set()
    wf_name_period = f'{wf_name}.'
    for name in called_task_names_raw:
      if name.startswith(wf_name_period):
        self.wf_prefix_needers.add(name[len(wf_name_period):].split('.')[0])
      # also add the virtual unscattered name of a scattered call, as a valid prefix that should get
      # the workflow name prefixed to it when seen.
      wlwsctr_index = name.find('_WLWSCTR')
      if wlwsctr_index != -1:
        unscattered_callas = name[:wlwsctr_index]
        if unscattered_callas.startswith(wf_name_period):
          self.wf_prefix_needers.add(unscattered_callas[len(wf_name_period):].split('.')[0])

    for structvar in local_structvar_names:
      self.wf_prefix_needers.add(structvar)

  def EXPLICIT_NAME(self, node):
    first_name = node.value.split('.')[0]
    if first_name != self.wf_name and first_name in self.wf_prefix_needers:
      return Token('EXPLICIT_NAME', f'{self.wf_name}.{node.value}')
    return node
