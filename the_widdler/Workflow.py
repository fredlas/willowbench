import copy
from enum import Enum
from lark import Tree, Token, visitors

from tf_common import WILLOWNULL, FINALIZED_TYPES, is_resolved, decl_meta_name, decl_meta_type
from tf_run_time import CommandFinishedTF, FileNowAvailableTF, StdoutNowAvailableTF, FileContentsNowAvailableTF, GlobResultNowAvailableTF, SizeNowAvailableTF
from tf_resolution import ResolutionTF
from tf_template_time import TaskOutputDeclsTF, AllOtherDeclsTF, WorkflowPrefixGuarantorTF, NonRootInputDeclsTF, ImpliedNameTF, parse_decl_subtree
import util
from util import log_error, log_debug
from scatter import ScatterTask

DIFF_ONLY_DEBUG_LOGS = False

class ResolutionState(Enum):
  RESOLVED_TRUE = 1
  RESOLVED_FALSE = 2
  UNRESOLVED = 3

class PlainTask:
  def __init__(self, new_task, unitname, job_context):
    self.output_var_names = []
    self.optional_var_names = set()
    self.the_task = TaskOutputDeclsTF(self.output_var_names, self.optional_var_names).transform(new_task)
    self.task_pending = True
    self.job_context = job_context
    self.resolutions_tf = ResolutionTF(unitname, self.job_context)

  def finish_task(self, command_finished_tf):
    self.the_task = command_finished_tf.transform(self.the_task)
    self.task_pending = False

  def pending(self):
    return self.task_pending

  # returns true if anything changed
  def apply_resolutions_tf_to_task(self):
    try:
      new_the_task = self.resolutions_tf.transform(self.the_task)
    except visitors.VisitError as e:
      raise e.orig_exc
    if new_the_task != self.the_task:
      self.the_task = new_the_task
      return True
    return False

  def accept_glob_result_lists(self, glob_lists):
    res_str = ''
    for glb, the_list in glob_lists.items():
      res_str += f'{glb} found {len(the_list)} files, '
    log_debug(f'now delivering glob results to task {self.resolutions_tf.unitname}: {res_str}')
    self.the_task = GlobResultNowAvailableTF(glob_lists).transform(self.the_task)

  def accept_cmd_stdout(self, cmd_stdout):
    log_debug(f'now delivering stdout to task {self.resolutions_tf.unitname}')
    self.the_task = StdoutNowAvailableTF(self.resolutions_tf.unitname, cmd_stdout).transform(self.the_task)

  def accept_file_contents(self, file_contents):
    self.the_task = FileContentsNowAvailableTF(file_contents).transform(self.the_task)

  def get_task_outputs(self, task_call_as_name):
    outputs = {}
    for var_name in self.output_var_names:
      fqvn = f'{task_call_as_name}.{var_name}'
      rhs_tok = self.job_context.resolved_variables.get(fqvn)
      if rhs_tok and is_resolved(rhs_tok):
        outputs[var_name] = rhs_tok
      elif var_name not in self.optional_var_names:
        log_error(f'get_task_outputs() called when {task_call_as_name} didnt yet have output {var_name} ready')
        return None
    return outputs

  def __repr__(self):
    def to_str_nonterm(node, depth):
      treedump = f'{depth}{node.data}\n'
      for child in node.children:
        if type(child) == Tree:
          if child.data == 'command':
            treedump += to_str_cmd(child, depth + '  ')
          else:
            treedump += to_str_nonterm(child, depth + '  ')
        else:
          treedump += to_str_term(child, depth + '  ')
      return treedump
    def to_str_term(node, depth):
      if node is None:
        return f'{depth}weird, here is a none\n'
      else:
        return f'{depth}{node.type} "{node.value}"\n'
    def to_str_cmd(node, depth):
      vars_found = 0
      strs_found = 0
      others_found = 0
      cmdvars = ''
      for c in node.children:
        if type(c) == Tree and c.data == 'cmdvarplaceholder':
          vars_found += 1
          cmdvars += to_str_nonterm(c, depth + '  ')
        elif type(c) == Token and c.type == 'FINALIZED_STR':
          strs_found += 1
        else:
          others_found += 1
      return f'{depth}command node: {vars_found} cmdvarplaceholder children, {strs_found} FINALIZED_STR children, {others_found} other children\n{cmdvars}'
    return to_str_nonterm(self.the_task, '')


class ConditionalTask:
  def __init__(self, siblings):
    self.cond_expr = siblings[0]
    self.calllikes = siblings[1:]

  def try_resolve(self, resolution_tf, resolved_variables, root_wf_name):
    if type(self.cond_expr) == Tree:
      self.cond_expr = resolution_tf.transform(self.cond_expr)
    if type(self.cond_expr) == Tree:
      return ResolutionState.UNRESOLVED
    assert type(self.cond_expr) == Token
    if self.cond_expr.type == 'EXPLICIT_NAME':
      lookup_resvar = self.cond_expr.value
      if not lookup_resvar.startswith(root_wf_name):
        lookup_resvar = f'{root_wf_name}.{lookup_resvar}'

      if lookup_resvar in resolved_variables:
        the_val = resolved_variables[lookup_resvar].value
      else:
        return ResolutionState.UNRESOLVED
    else:
      assert 'FINALIZED' in self.cond_expr.type
      the_val = self.cond_expr.value
    if type(the_val) in (bool, int, float, str, list):
      return ResolutionState.RESOLVED_TRUE if bool(the_val) else ResolutionState.RESOLVED_FALSE
    raise ValueError(f'unexpected type in _get_bool_resolution_state: {type(the_val)}, {str(the_val)}')


class Workflow:
  def __init__(self, wf_name, wf_tree, job_context, wdl_map, wdl_source_fname):
    self.my_wf_name = wf_name
    self.wdl_source_fname = wdl_source_fname
    self.wf_tree = wf_tree
    self.job_context = job_context
    self.wdl_map = wdl_map
    self.last_repr_parts = {}

    self.my_output_names = []
    for output_decl in wf_tree.find_data('output_decls'):
      for child in output_decl.children:
        self.my_output_names.append(child.children[1].value)

    self.local_structvar_names = []
    for decl in wf_tree.find_data('var_decl_thing'):
      decl_meta = decl.children[0]
      if decl_meta_type(decl_meta) == 'Struct':
        self.local_structvar_names.append(decl_meta_name(decl_meta))
    for decl in wf_tree.find_data('pending_root_input_var_decl'):
      decl_meta = decl.children[0]
      if decl_meta_type(decl_meta) == 'Struct':
        self.local_structvar_names.append(decl_meta_name(decl_meta))

    self.called_wfs = {} # keys are call-as names, values are Workflow
    self.called_tasks = {} # keys are call-as names, values are PlainTask
    self.considered_conditional_calls = set() # call-as names
    self.unresolved_conditionals = []
    self.unresolved_scatters = []

    # array of many Tree('var_decl_thing', ...) that were scattered from individual out-decls of
    # scattered tasks. This array is the home for those decls - decls normally live in a called task's
    # Lark tree, but these ones aren't associated with a task in that way (they are associated with
    # many simultaneously), so we hold them here.
    self.gather_decls = []
    self.wf_resolutions_tf = ResolutionTF(self.my_wf_name, job_context)
    # kick off the initial calls
    self._process_calllike_treenodes(self.wf_tree.children)
    # input-less tasks might be able to run immediately
    self.resolve_all_possible()

  def __repr__(self):
    return util.wf_repr_diff(self) if DIFF_ONLY_DEBUG_LOGS else util.wf_repr_no_diff(self)

  def _get_call_info(self, the_call_node):
    target_callable_name = the_call_node.children[0].value
    if the_call_node.data == 'call': # always prepend our wf name, even if referring to an imported wf
      implicit_call_name = target_callable_name.split('.')[-1]
      call_as_name = f'{self.my_wf_name}.{implicit_call_name}'
    elif the_call_node.data == 'call_as':
      call_as_name = f'{self.my_wf_name}.{the_call_node.children[1].value}'
    else:
      raise ValueError(f'expected either call or call_as, got {the_call_node.data}')
    return target_callable_name, call_as_name

  # returns (call_as_name, task_template, wf_template, wdl_src_fname) where wdl_src_fname is where
  # the returned template was found. Exactly one of task/wf template should be None.
  def _build_one_called_task_or_wf(self, the_call_node):
    target_callable_name, call_as_name = self._get_call_info(the_call_node)

    def get_call_inputs_subtree(node):
      for c in node.children:
        if type(c) == Tree and c.data == 'call_inputs':
          return c
      return None
    call_inputs = get_call_inputs_subtree(the_call_node)

    res = self.wdl_map.lookup(target_callable_name, self.wdl_source_fname)
    if res is None:
      split_target = target_callable_name.split('.', 1)
      if len(split_target) == 2:
        maybe_wdl_srcname, maybe_true_target_name = split_target
        res = self.wdl_map.lookup(maybe_true_target_name, maybe_wdl_srcname)
        log_error(f'failed to find wdl srcname {maybe_wdl_srcname}, callable target name {maybe_true_target_name}')
    if res is None:
      raise ValueError(f'no unit named {target_callable_name} found') from None
    template_to_call, is_wf, wdl_src_fname = res
    if is_wf:
      called_wf = copy.deepcopy(template_to_call)
      if call_inputs:
        called_wf = NonRootInputDeclsTF(call_inputs, call_as_name).transform(called_wf)
        called_wf = ImpliedNameTF(call_as_name).transform(called_wf)
      return call_as_name, None, called_wf, wdl_src_fname
    else: # it's a task
      called_task = copy.deepcopy(template_to_call)
      called_task = NonRootInputDeclsTF(call_inputs, call_as_name).transform(called_task)
      called_task = ImpliedNameTF(call_as_name).transform(called_task)
      return call_as_name, called_task, None, wdl_src_fname

  def _add_actual_call(self, node):
    call_as_name, new_task_tree, new_wf_tree, wdl_src_fname = self._build_one_called_task_or_wf(node)
    assert new_task_tree is None or new_wf_tree is None
    if new_task_tree:
      self.called_tasks[call_as_name] = PlainTask(new_task_tree, call_as_name, self.job_context)
    elif new_wf_tree:
      self.called_wfs[call_as_name] = Workflow(call_as_name, new_wf_tree, self.job_context,
                                               self.wdl_map, wdl_src_fname)
    else:
      raise ValueError('_add_actual_call: both new_task_tree and new_wf_tree are None')

  # If an optional var receives its value from the output of a task inside a conditional, we need
  # to be able to propagate a WILLOWNULL into that var. To do that, the source variable needs to be
  # named correctly - i.e. get hit by WorkflowPrefixGuarantorTF. But that TF needs to be told the
  # names to look out for. Before this function, uncalled calls inside false conditionals weren't
  # added to that list of names.
  def _add_all_conditional_descendants_names(self, node):
    if type(node) is not Tree:
      return
    if node.data in ('call', 'call_as'):
      target_callable_name, call_as_name = self._get_call_info(node)
      self.considered_conditional_calls.add(call_as_name)
    for c in node.children:
      self._add_all_conditional_descendants_names(c)

  def _process_calllike_treenodes(self, treenodes):
    anything_changed = False
    for node in treenodes:
      if type(node) is not Tree:
        continue
      if node.data in ('call', 'call_as'):
        self._add_actual_call(node)
        anything_changed = True
      elif node.data == 'conditional':
        the_cond_task = ConditionalTask(node.children)
        for c in the_cond_task.calllikes:
          self._add_all_conditional_descendants_names(c)
        self.unresolved_conditionals.append(the_cond_task)
        anything_changed = True
      elif node.data == 'scatter':
        calllikes_to_be_scattered = node.children[2:]
        new_scatter = ScatterTask(node.children[0].value, node.children[1],
                                  calllikes_to_be_scattered, self.gather_decls, self.wdl_map, self.wdl_source_fname)
        self.unresolved_scatters.append(new_scatter)
        anything_changed = True

    self._apply_transformer_to_all(AllOtherDeclsTF())
    wf_prefix_guarantor_tf = WorkflowPrefixGuarantorTF(self.my_wf_name, list(self.called_tasks.keys()) + list(self.called_wfs.keys()) + list(self.considered_conditional_calls), self.local_structvar_names)
    self._apply_transformer_to_all(wf_prefix_guarantor_tf)
    for cond_obj in self.unresolved_conditionals:
      if type(cond_obj.cond_expr) is Tree:
        cond_obj.cond_expr = wf_prefix_guarantor_tf.transform(cond_obj.cond_expr)
    for scatter_obj in self.unresolved_scatters:
      if type(scatter_obj.scatter_source) is Tree:
        scatter_obj.scatter_source = wf_prefix_guarantor_tf.transform(scatter_obj.scatter_source)

    return anything_changed

  def _nullify_outputs_for_calllikes(self, calllikes):
    for node in calllikes:
      if not isinstance(node, Tree):
        continue

      if node.data in ('call', 'call_as'):
        target_callable_name, call_as_name = self._get_call_info(node)

        res = self.wdl_map.lookup(target_callable_name, self.wdl_source_fname)
        if res is None:
          raise ValueError(f'no unit named {target_callable_name} found') from None
        template_to_call, _, _ = res

        for outdecls_section in template_to_call.find_data('output_decls'):
          for orig_outdecl in outdecls_section.find_data('decl'):
            decl_meta = parse_decl_subtree(orig_outdecl.children)
            self.job_context.resolved_variables.insert(f'{call_as_name}.{decl_meta_name(decl_meta)}', WILLOWNULL)
      elif node.data == 'conditional':
        self._nullify_outputs_for_calllikes(node.children[1:])
      elif node.data == 'scatter':
        raise ValueError('scatter inside a conditional that evaluates to false should never be processed...?!?')

  def resolution_pass(self):
    changed = self._apply_resolutions_to_wf()
    changed |= self._apply_resolutions_to_child_wfs()
    changed |= self._apply_resolutions_to_tasks()
    changed |= self._resolve_conditionals()
    changed |= self._resolve_scatters()
    changed |= self._resolve_gathers()
    return changed

  def resolve_all_possible(self):
    log_debug('beginning resolve_all_possible')
    while self.resolution_pass():
      pass
    sep = '========================================================================'
    # log_debug(f'{sep}\nAfter all resolutions:{str(self)}\n{sep}\n')
    log_debug('ending resolve_all_possible')

  def _apply_transformer_to_all(self, tf):
    self.wf_tree = tf.transform(self.wf_tree)
    for task in self.called_tasks.values():
      task.the_task = tf.transform(task.the_task)

  def _apply_resolutions_to_wf(self):
    try:
      new_workflow = self.wf_resolutions_tf.transform(self.wf_tree)
    except visitors.VisitError as e:
      raise e.orig_exc
    if self.wf_tree != new_workflow:
      self.wf_tree = new_workflow
      return True
    return False

  def _apply_resolutions_to_child_wfs(self):
    changed = False
    for wf in self.called_wfs.values():
      changed |= wf.resolution_pass()
    return changed

  def _apply_resolutions_to_tasks(self):
    changed = False
    for task in self.called_tasks.values():
      if task.pending() and task.apply_resolutions_tf_to_task():
        changed = True
    return changed

  def _resolve_conditionals(self):
    changed = False
    remaining_conditionals = []
    for unresolved in self.unresolved_conditionals:
      resolution = unresolved.try_resolve(self.wf_resolutions_tf, self.job_context.resolved_variables, self.my_wf_name)
      if resolution == ResolutionState.UNRESOLVED:
        remaining_conditionals.append(unresolved)
      elif resolution == ResolutionState.RESOLVED_TRUE:
        changed |= self._process_calllike_treenodes(unresolved.calllikes)
      else: # RESOLVED_FALSE
        self._nullify_outputs_for_calllikes(unresolved.calllikes)
    self.unresolved_conditionals = remaining_conditionals
    return changed

  def _resolve_scatters(self):
    changed = False
    remaining_scatters = []
    for unresolved in self.unresolved_scatters:
      calllikes = unresolved.try_duplicate_out_calllikes(self.wf_resolutions_tf, self.job_context.resolved_variables,
                                                         self.my_wf_name)
      if calllikes is None:
        remaining_scatters.append(unresolved)
      else:
        changed |= self._process_calllike_treenodes(calllikes)
    self.unresolved_scatters = remaining_scatters
    return changed

  def _resolve_gathers(self):
    changed = False
    for i, gather_decl_node in enumerate(self.gather_decls):
      if gather_decl_node is None:
        continue
      if not (type(gather_decl_node) is Tree and gather_decl_node.data == 'var_decl_thing'):
        raise ValueError(f'unexpected gather_decl_node: {gather_decl_node}') from None

      try:
        new_node = self.wf_resolutions_tf.transform(gather_decl_node)
        if new_node != gather_decl_node:
          self.gather_decls[i] = new_node if new_node is not visitors.Discard else None
          changed = True
      except visitors.VisitError as e:
        raise e.orig_exc
    return changed

  def get_workflow_outputs(self):
    def _get_one_workflow_output(wf_name, output_name, res_vars_obj):
      token = res_vars_obj[f'{wf_name}.{output_name}']
      if token.type == 'FINALIZED_FILE':
        return token.value.cloud_path
      if token.type == 'FINALIZED_FILEARRAY':
        ret_arr = []
        for clf in token.value:
          ret_arr.append('' if clf is None else clf.cloud_path)
        return ret_arr
      if token.type in FINALIZED_TYPES:
        return token.value
      raise ValueError(f'Unexpected token type for output {output_name}: {token.type}')
    ret = {}
    for output_name in self.my_output_names:
      ret[output_name] = _get_one_workflow_output(self.my_wf_name, output_name,
                                                  self.job_context.resolved_variables)
    return ret

  def any_task_pending(self):
    if any(x.pending() for x in self.called_tasks.values()):
      return True
    if any(wf.any_task_pending() for wf in self.called_wfs.values()):
      return True
    return False

  def notify_command_finished(self, task_call_as_name, status):
    for wf in self.called_wfs.values():
      wf.notify_command_finished(task_call_as_name, status)
    if task_call_as_name not in self.called_tasks:
      return
    log_debug(f'{self.my_wf_name} will now mark {task_call_as_name} as done in WDL parse tree',
              self.job_context.job_id)
    self.called_tasks[task_call_as_name].finish_task(CommandFinishedTF(task_call_as_name, status))
    # resolve_all_possible() will skip this one because it's no longer pending, but there might
    # be some final things to do, like output { String foo = "const" } to notice.
    self.called_tasks[task_call_as_name].apply_resolutions_tf_to_task()
    self.resolve_all_possible()

  def notify_files_available(self):
    for wf in self.called_wfs.values():
      wf.notify_files_available()
    self._apply_transformer_to_all(FileNowAvailableTF(self.job_context))
    self.resolve_all_possible()

  def accept_size_response(self, size_results):
    for wf in self.called_wfs.values():
      wf.accept_size_response(size_results)
    self._apply_transformer_to_all(SizeNowAvailableTF(size_results))
    self.resolve_all_possible()

  def accept_glob_result_lists(self, task_call_as_name, glob_lists):
    if task_call_as_name in self.called_tasks:
      self.called_tasks[task_call_as_name].accept_glob_result_lists(glob_lists)
    else:
      for wf in self.called_wfs.values():
        wf.accept_glob_result_lists(task_call_as_name, glob_lists)

  def accept_cmd_stdout(self, task_call_as_name, cmd_stdout):
    if task_call_as_name in self.called_tasks:
      self.called_tasks[task_call_as_name].accept_cmd_stdout(cmd_stdout)
    else:
      for wf in self.called_wfs.values():
        wf.accept_cmd_stdout(task_call_as_name, cmd_stdout)

  def accept_file_contents(self, task_call_as_name, file_contents):
    if task_call_as_name in self.called_tasks:
      self.called_tasks[task_call_as_name].accept_file_contents(file_contents)
    else:
      for wf in self.called_wfs.values():
        wf.accept_file_contents(task_call_as_name, file_contents)

  def get_task_outputs(self, task_call_as_name):
    if task_call_as_name in self.called_tasks:
      return self.called_tasks[task_call_as_name].get_task_outputs(task_call_as_name)
    for wf in self.called_wfs.values():
      res = wf.get_task_outputs(task_call_as_name)
      if res is not None:
        return res
    return None

  def find_task(self, task_name):
    if task_name in self.called_tasks:
      return self.called_tasks[task_name]
    for wf in self.called_wfs.values():
      task = wf.find_task(task_name)
      if task:
        return task
    return None

  def notify_task_cache_hit(self, task_name, outputs):
    if task_name in self.called_tasks:
      del self.called_tasks[task_name]
      self.resolve_all_possible()
      return True

    for wf in self.called_wfs.values():
      if wf.notify_task_cache_hit(task_name, outputs):
        return True
    return False
