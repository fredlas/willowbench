import copy

from lark import Token, Tree, Transformer
from tf_common import token_from_finalized_arr_index
import util
from util import log_warn

def _get_scattered_output_fqn(wf_name, orig_callas, invar, scatter_ind, outdecl):
  suffix = make_scatter_task_suffix(invar, scatter_ind)
  return util.splice_prefix(wf_name, f'{orig_callas}{suffix}.{outdecl}')

def make_scatter_task_suffix(var_to_scatter, cur_index):
  return f'_WLWSCTR{var_to_scatter}_{cur_index}'

class GatherPrep:
  def __init__(self, new_decl_type, is_optional, orig_decl_name, orig_callas_name, scattered_input_varname):
    self.new_decl_type = new_decl_type
    self.is_optional = is_optional
    self.orig_decl_name = orig_decl_name
    self.orig_callas_name = orig_callas_name
    self.new_decl_name = f'{orig_callas_name}.{orig_decl_name}'
    self.scattered_input_varname = scattered_input_varname

  def __repr__(self):
    return f'GatherPrep: type {self.new_decl_type} name {self.new_decl_name} origcallas {self.orig_callas_name} inputvarname {self.scattered_input_varname} optional {self.is_optional}'

  def make_new_decl(self, num_scattered, wf_name):
    arr_kids = []
    for i in range(num_scattered):
      the_name = _get_scattered_output_fqn(wf_name, self.orig_callas_name,
                                           self.scattered_input_varname, i, self.orig_decl_name)
      arr_kids.append(Token('EXPLICIT_NAME', the_name))

    return Tree('var_decl_thing', [Tree('decl_meta',
                                        [Token('DECL_TYPE', self.new_decl_type),
                                         Token('DECL_NAME', self.new_decl_name),
                                         Token('OPTIONALITY', self.is_optional)]),
                                   Tree('array', arr_kids)])


# node is expected to be a Tree(Token('RULE', 'type'), [...])
def _get_calllike_names(nodes):
  names = []
  for node in nodes:
    if not isinstance(node, Tree):
      continue

    if node.data in ('call', 'call_as'):
      name = node.children[0].value if node.data == 'call' else node.children[1].value
      names.append(name)
    elif node.data == 'conditional':
      names.extend(_get_calllike_names(node.children[1:]))
    elif node.data == 'scatter':
      names.extend(_get_calllike_names(node.children[2:]))
  return names

def _extract_type_from_typesubtree(node):
  assert node.data.type == 'RULE' and node.data.value == 'type'
  is_optional = any(isinstance(child, Tree) and child.data == 'optional' for child in node.children)
  type_info_tree = node.children[0]
  type_info_rule_name = type_info_tree.data.value

  res_type = ''
  if type_info_rule_name == 'type_noncompound':
    actual_type_tree = type_info_tree.children[0]
    type_map = { 'type_is_int': 'Int',
                 'type_is_float': 'Float',
                 'type_is_string': 'String',
                 'type_is_bool': 'Boolean',
                 'type_is_file': 'File',
                 'type_is_struct': 'Struct' }
    actual_type_rule_name = actual_type_tree.data.value
    if actual_type_rule_name in type_map:
      res_type = type_map[actual_type_rule_name]
    else:
      raise ValueError(f'unsupported primitive type {actual_type_rule_name}')
  elif type_info_rule_name == 'type_is_array':
    inner_type_tree = node.children[1]
    inner_type_str, _ = _extract_type_from_typesubtree(inner_type_tree)
    if inner_type_str.startswith('Array') and 'File' in inner_type_str:
      raise ValueError('Array of Array of Files is not supported.')
    res_type = 'Array' + inner_type_str
  else:
    raise ValueError(f'unsupported type {type_info_rule_name} in _extract_type_from_typesubtree') from None

  return res_type, is_optional

# so long as the later users use name unscattered_call_as_name.the_out, and so long as the
# scattered tasks were writing into [scattered_CA_names].the_out, resolving this artificial
# `Array[Type] unscattered_call_as_name.the_out = Tree(array, [])` decl will do what we need.
#
# for each outdecl e.g. "Type the_out" found among calllikes_to_be_scattered e.g. "CalledTask"
#   build a neither-decl "Array[Type] the_out" scoped to the unscattered callas-name (CalledTask.the_out)
#   and with RHS node array(CalledTask_WLWSCTRthe_in1.the_out, CalledTask_WLWSCTRthe_in2.the_out, ...)
def _find_and_prep_gathers(nodes, in_conditional, gather_preps, scattered_input_varname, wdl_map, home_wdl_fname):
  for node in nodes:
    if not isinstance(node, Tree):
      continue

    if node.data in ('call', 'call_as'):
      orig_callas_name = node.children[0].value if node.data == 'call' else node.children[1].value
      target_task_name = node.children[0].value

      # we're prepping the gather here, which cares about the called task/wf's outputs, which the call
      # subtree doesn't actually have reference to. so we have to go look them up in the templates.
      # (both workflow and task are fine here: we are only working with output_decls, which both have
      #  in the same format).
      res = wdl_map.lookup(target_task_name, home_wdl_fname)
      if res is None:
        raise ValueError(f'no unit named {target_task_name} found') from None
      template_to_call, _is_wf, _src_wdl_fname = res

      for outdecls_section in template_to_call.find_data('output_decls'):
        for orig_outdecl in outdecls_section.find_data('decl'):
          base_type, is_optional = _extract_type_from_typesubtree(orig_outdecl.children[0])
          is_optional = is_optional or in_conditional
          new_decl_type = f'Array{base_type}'
          orig_decl_name = orig_outdecl.children[1].value

          key = (orig_callas_name, orig_decl_name)
          if key not in gather_preps:
            gather_preps[key] = GatherPrep(new_decl_type, is_optional, orig_decl_name,
                                           orig_callas_name, scattered_input_varname)
          else:
            # If we see the same output again, make sure it's optional if either path is conditional.
            existing_prep = gather_preps[key]
            existing_prep.is_optional = existing_prep.is_optional or is_optional
    elif node.data == 'conditional':
      _find_and_prep_gathers(node.children[1:], True, gather_preps, scattered_input_varname, wdl_map, home_wdl_fname)
    elif node.data == 'scatter':
      # Nested scatter. The "outputs" of this scatter are the gathered outputs of the calls within it.
      inner_scatter_varname = node.children[0].value
      inner_calllikes = node.children[2:]
      inner_gather_preps = {}
      _find_and_prep_gathers(inner_calllikes, in_conditional, inner_gather_preps,
                             inner_scatter_varname, wdl_map, home_wdl_fname)
      for inner_prep in inner_gather_preps.values():
        # Each inner gather prep represents an "output" of the inner scatter. Its type is
        # `inner_prep.new_decl_type` (which is an Array type). We now create/update a gather prep
        # for the outer scatter.
        base_type = inner_prep.new_decl_type
        is_optional = inner_prep.is_optional
        new_decl_type = f'Array{base_type}'
        orig_decl_name = inner_prep.orig_decl_name
        orig_callas_name = inner_prep.orig_callas_name

        key = (orig_callas_name, orig_decl_name)
        if key not in gather_preps:
          gather_preps[key] = GatherPrep(new_decl_type, is_optional, orig_decl_name,
                                         orig_callas_name, scattered_input_varname)
        else:
          existing_prep = gather_preps[key]
          existing_prep.is_optional = existing_prep.is_optional or is_optional
    elif node.data not in ('decl'):
      log_warn(f'unexpected node in scatter body. expected call, call_as, or conditional; it was: {node.data}')

class ScatterTask:
  def __init__(self, scattered_varname, scatter_source, calllikes, gather_decls_ref, wdl_map, home_wdl_fname):
    self.scattered_varname = scattered_varname
    self.scatter_source = scatter_source
    self.calllikes = calllikes
    self.gather_decls_ref = gather_decls_ref
    # Gathering the output of a scatter has two steps. First, we track a GatherPrep for each scattered
    # input var, tracking the (not-yet-scattered) "templates" of the decls that we're planning to
    # insert scattered into gather_decls.
    # Later, when we are ready to duplicate out the to-be-scattered calllikes into actual task calls,
    # we use many calls of GatherPrep.make_new_decl() to make the scattered decls.
    # The reason we have to first prepare is that the RHS of those decls are arrays
    # of EXPLICIT_NAMEs built from indexing through len(scattered input array), and we don't yet
    # know what length the array will resolve to.
    # Therefore _find_and_prep_gathers() must be called before try_duplicate_out_calllikes().
    gather_preps = {}
    _find_and_prep_gathers(calllikes, False, gather_preps, scattered_varname, wdl_map, home_wdl_fname)
    self.gather_prep = list(gather_preps.values())

  def __str__(self):
    return f'scattering into {str(self.scattered_varname)} from {str(self.scatter_source)}, calllikes {str(self.calllikes)}'

  # returns list of new calllike Trees if resolved, otherwise None
  def try_duplicate_out_calllikes(self, resolution_tf, resolved_vars, wf_name):
    if type(self.scatter_source) == Tree:
      self.scatter_source = resolution_tf.transform(self.scatter_source)
    if type(self.scatter_source) == Tree:
      return None

    assert type(self.scatter_source) == Token
    if self.scatter_source.type == 'EXPLICIT_NAME':
      if self.scatter_source.value in resolved_vars:
        self.scatter_source = resolved_vars[self.scatter_source.value]
      else:
        spliced_name = util.splice_prefix(wf_name, self.scatter_source.value) # HACK, sort of...
        if spliced_name in resolved_vars:
          self.scatter_source = resolved_vars[spliced_name]
        else:
          return None

    if 'ARRAY' not in self.scatter_source.type:
      raise ValueError(f'expected resolved scatter to be an array, not {self.scatter_source.type}')

    self._convert_gather_preps_to_decls(len(self.scatter_source.value), wf_name)
    if self.gather_prep: # still some gather decls not yet ready to be scattered
      return None

    ret = []
    calllike_names = _get_calllike_names(self.calllikes)
    for i in range(len(self.scatter_source.value)):
      one_scatter_tok = token_from_finalized_arr_index(self.scatter_source, i)
      scatterer_tf = ScattererTF(self.scattered_varname, one_scatter_tok, i, wf_name, calllike_names)
      for cur_calllike_template in self.calllikes:
        cur_calllike_instance = copy.deepcopy(cur_calllike_template)
        ret.append(scatterer_tf.transform(cur_calllike_instance))
    return ret

  def _convert_gather_preps_to_decls(self, num_scattered, wf_name):
    for i, prep in enumerate(self.gather_prep):
      self.gather_decls_ref.append(prep.make_new_decl(num_scattered, wf_name))
      self.gather_prep[i] = None
    self.gather_prep = [x for x in self.gather_prep if x is not None]

class ScattererTF(Transformer):
  def __init__(self, target_varname, replace_with_token, the_index, wf_name, calllike_names):
    super().__init__(visit_tokens=True)
    self.target_varname = target_varname
    self.suffix = make_scatter_task_suffix(self.target_varname, the_index)
    self.replace_with_token = replace_with_token
    self.prefixed_target_varname = util.splice_prefix(wf_name, self.target_varname)
    self.calllike_names = calllike_names
    self.wf_name = wf_name

  def EXPLICIT_NAME(self, node):
    if node.value in (self.target_varname, self.prefixed_target_varname):
      return self.replace_with_token

    val = node.value
    wf_prefix = f'{self.wf_name}.'
    if val.startswith(wf_prefix):
        val = val[len(wf_prefix):]

    parts = val.split('.', 1)
    if len(parts) == 2:
        call_name, rest_of_name = parts
        if call_name in self.calllike_names:
            new_name = f'{call_name}{self.suffix}.{rest_of_name}'
            return Token('EXPLICIT_NAME', f'{self.wf_name}.{new_name}')

    return node

  def call(self, c):
    target_task_name = c[0].value
    call_as_name_token = Token('CNAME', target_task_name + self.suffix)
    return Tree('call_as', [c[0], call_as_name_token] + c[1:])

  def call_as(self, c):
    orig_call_as_name = c[1].value
    if self.suffix in orig_call_as_name:
      return Tree('call_as', c)
    call_as_name_token = Token('CNAME', orig_call_as_name + self.suffix)
    return Tree('call_as', [c[0], call_as_name_token] + c[2:])
