from dataclasses import dataclass
import os
from typing import Optional

from lark import Lark, Tree, Token, Transformer

import wdl_grammar
from tf_workflow_user_inputs import WorkflowUserInputsTF, RootWorkflowInputDeclsTF
from tf_template_time import ImpliedNameTF
from tf_common import CloudLocalFile, indexed_array_name, WILLOWNULL
from util import log_error,log_warn,log_info,log_debug # pylint: disable=unused-import
import struct_types_parse
from Workflow import Workflow

class ResolvedVariables:
  def __init__(self):
    self.the_dict = {}
    # Maps from (originating) fully-qualified variable name to actual S3 URL.
    # Can have entries with value '', meaning it was an optional output that didn't exist.
    self.available_cloudpaths = {}
    # Maps from FQVN to list of FQVNs (resolved_vars keys). Key is an originating_fqvn, value is
    # all FQVNs who have the key as their originator.
    self.interest = {}

  def __getitem__(self, key): return self.the_dict[key]
  def __contains__(self, key): return key in self.the_dict
  def get(self, key, default=None): return self.the_dict.get(key, default)
  def items(self): return self.the_dict.items()

  def _handle_clf(self, resd_var_fqvn, clf):
    if clf is None:
      return
    if clf.cloud_path is not None:
      self.notify_files_available({clf.originating_fqvn: clf.cloud_path})
    else:
      self._register_interest(resd_var_fqvn, clf.originating_fqvn)

  def insert(self, resd_var_fqvn, rhs_tok):
    self.the_dict[resd_var_fqvn] = rhs_tok
    if rhs_tok.type == 'PENDING_FILE':
      self._register_interest(resd_var_fqvn, rhs_tok.value.originating_fqvn)
    elif rhs_tok.type == 'PENDING_FILEARRAY':
      for i, clf in enumerate(rhs_tok.value):
        self._handle_clf(indexed_array_name(resd_var_fqvn, i), clf)
    elif rhs_tok.type == 'WILLOWNULL':
      # HACK we don't actually know what type the var is here. so, putting it in available_cloudpaths
      #      is technically wrong if not a File... but I think it should be harmless.
      self.notify_files_available({resd_var_fqvn: ''})
    elif rhs_tok.type == 'FINALIZED_FILE':
      # FINALIZED_FILE(ARRAY) should only come from cache here (shouldn't matter here though)
      self.notify_files_available({rhs_tok.value.originating_fqvn: rhs_tok.value.cloud_path})
    elif rhs_tok.type == 'FINALIZED_FILEARRAY':
      avail_dict = {}
      for clf in rhs_tok.value:
        if clf is not None:
          avail_dict[clf.originating_fqvn] = clf.cloud_path
      self.notify_files_available(avail_dict)
    elif rhs_tok.type == 'PROPAGATABLE_STRUCT':
      self._recursive_notify_files_available(rhs_tok.value, resd_var_fqvn)

  def _recursive_notify_files_available(self, cur_node, cur_name):
    if isinstance(cur_node, dict):
      for k,v in cur_node.items():
        self._recursive_notify_files_available(v, cur_name + '.' + k)
    elif isinstance(cur_node, list):
      for i, ele in enumerate(cur_node):
        self._recursive_notify_files_available(ele, indexed_array_name(cur_name, i))
    elif isinstance(cur_node, CloudLocalFile):
      self._handle_clf(cur_name, cur_node)

  def _register_interest(self, my_resd_var_key, originator_fqvn):
    self.interest.setdefault(originator_fqvn, []).append(my_resd_var_key)
    if originator_fqvn in self.available_cloudpaths:
      self._finalize_one_pending_resvar(my_resd_var_key)

  # files_map can have entries with value '', meaning it was an optional output that didn't exist
  def notify_files_available(self, files_map):
    self.available_cloudpaths.update(files_map)
    for originating_fqvn in files_map.keys():
      interested = self.interest.get(originating_fqvn, [])
      for resd_var_key in interested:
        array_delim_ind = resd_var_key.find('_WILLOWindex')
        if array_delim_ind > -1:
          var_stem = resd_var_key[:array_delim_ind]
          the_index = int(resd_var_key[array_delim_ind+len('_WILLOWindex'):])
          self._finalize_one_pending_resvar(var_stem, the_index)
        else:
          self._finalize_one_pending_resvar(resd_var_key)

  # convert a resolved_vars File entry to finalized (if it was pending)
  def _finalize_one_pending_resvar(self, resd_varname, array_index=None):
    rhs_tok = self.the_dict[resd_varname]
    if rhs_tok.type == 'PENDING_FILE':
      the_cloudpath = self.available_cloudpaths.get(rhs_tok.value.originating_fqvn)
      if the_cloudpath is None:
        log_warn(f'_finalize_one_pending_resvar called on {resd_varname} whose originating_fqvn wasnt in self.available_cloudpaths... i think this should never print?')
      if the_cloudpath == '':
        self.the_dict[resd_varname] = WILLOWNULL
      else:
        rhs_tok.value.cloud_path = the_cloudpath
        self.the_dict[resd_varname] = Token('FINALIZED_FILE', rhs_tok.value)
    elif rhs_tok.type == 'PENDING_FILEARRAY':
      if rhs_tok.value[array_index] is None:
        raise ValueError(f'{resd_varname} tried to finalize index {array_index}, whose entry is None')
      the_cloudpath = self.available_cloudpaths.get(rhs_tok.value[array_index].originating_fqvn)
      if the_cloudpath is not None:
        if the_cloudpath == '':
          rhs_tok.value[array_index] = None
        else:
          rhs_tok.value[array_index].cloud_path = the_cloudpath
      if all(x is None or x.cloud_path is not None for x in rhs_tok.value):
        self.the_dict[resd_varname] = Token('FINALIZED_FILEARRAY', rhs_tok.value)

@dataclass
class JobContext:
  """Holds all job-wide state and configuration."""
  job_id: str
  useremail: str
  s3_bucket: str
  queued_tasks_execs: list # JSONs to be sent to core
  queued_size_queries: list
  resolved_variables: ResolvedVariables
  struct_field_types: dict

  def insert_resolved_var(self, resd_var_fqvn, rhs_tok):
    self.resolved_variables.insert(resd_var_fqvn, rhs_tok)

class _WdlParser:
  """Parses a root WDL file and all its imports recursively."""
  def __init__(self, workflow_dir, parser, struct_field_types):
    self.workflow_dir = workflow_dir
    self.parser = parser
    self.struct_field_types = struct_field_types
    self.all_wdls = {}

  def parse_main_wdl(self, root_filename, file_contents_override=None):
    if file_contents_override:
        self._parse_wdl_file_contents(root_filename, file_contents_override, ROOT_WF_IMPORT_AS_NAME, True)
    else:
        self._parse_wdl_file(root_filename, ROOT_WF_IMPORT_AS_NAME, True)
    return self.all_wdls

  @staticmethod
  def _get_task_templates(root):
    ret = {}
    for child in root.children:
      if type(child) == Tree and child.data == 'task':
        ret[child.children[0].value] = child
    return ret

  @staticmethod
  def _get_workflow_template(root):
    for child in root.children:
      if type(child) == Tree and child.data == 'workflow':
        return child
    return None

  # returns path_to_wdl_file, import_as_name (import_as_name can default to path_to_wdl_file)
  @staticmethod
  def _hacky_extract_import_path(import_node):
    if type(import_node.children[0]) is Token:
      fname = import_node.children[0].value
    else: # should be a weird Tree(Token(RULE), [Token(ESCAPED_STRING, '"File.wdl"')]) thing.
      fname = import_node.children[0].children[0].value
      assert fname.startswith('"') and fname.endswith('"')
      fname = fname[1:-1]
    import_as_name = fname.split('.')[0]
    if len(import_node.children) > 1:
      import_as_name = import_node.children[1].value
    if len(import_node.children) > 2:
      assert import_node.children[2].data == 'import_alias'
      raise ValueError('import_alias not yet supported.')
    return fname, import_as_name

  def _do_imports(self, root):
    for import_node in root.find_data('import_doc'):
      fname, import_as_name = self._hacky_extract_import_path(import_node)

      if import_as_name in self.all_wdls:
        if self.all_wdls[import_as_name].filename != fname:
          raise ValueError(f'importing wdl file {fname} as {import_as_name}, but that as-name already exists as wdl file {self.all_wdls[import_as_name].filename}')
        continue

      # check if we already parsed this file under another alias
      existing_wdl_source = None
      for wdl in self.all_wdls.values():
        if wdl.filename == fname:
          existing_wdl_source = wdl
          break

      if existing_wdl_source:
        self.all_wdls[import_as_name] = existing_wdl_source
      else:
        self._parse_wdl_file(fname, import_as_name, False)

  def _parse_wdl_file_contents(self, filename, file_contents, import_as_name, is_root):
    struct_types_parse.parse_all_structs(file_contents, self.struct_field_types)
    file_contents = file_contents.replace('Array[Array[String]]', 'Matrix[String]') # TODO TODO TODO TODO HACK LOLL
    file_contents = file_contents.replace('Array[Array[Int]]', 'Matrix[Int]')
    file_contents = file_contents.replace('Array[Array[Float]]', 'Matrix[Float]')
    file_contents = file_contents.replace('Array[Array[Boolean]]', 'Matrix[Boolean]')
    if 'Array[Array' in file_contents:
      raise ValueError('only Array[Array[{String,Int,Float,Boolean}]] are currently supported')
    tree = self.parser.parse(file_contents)
    self.all_wdls[import_as_name] = WdlSource(self._get_workflow_template(tree), self._get_task_templates(tree),
                                        filename, is_root)
    self._do_imports(tree)

  def _parse_wdl_file(self, filename, import_as_name, is_root):
    with open(os.path.join(self.workflow_dir, filename), 'r', encoding='utf-8') as file:
      file_contents = file.read()
      self._parse_wdl_file_contents(filename, file_contents, import_as_name, is_root)

# This Transformer runs at 'task-template-time'. Sure, the original WDLJob object is a
# template, but within it, the tasks can be *even more* templatey. Specifically, thanks to the
# call_as mechanism, the task definition blocks can't be used to implicitly specify the tasks that
# will make up the workflow. Rather, must use the calls. So we first make task templates out of
# those task blocks, and then make actual "called tasks" by copying those templates, as directed by
# the calls.
#
# Anyways, everything in here is safe to do even on those task templates.
class FullyQualifiedNameTF(Transformer):
  def fq_name(self, children):
    assert children[1].type == 'CNAME'
    if type(children[0]) is Tree and children[0].data == 'left_name':
      the_name = f'{children[0].children[0].value}.{children[1].value}'
    elif type(children[0]) is Token and children[0].type == 'EXPLICIT_NAME':
      the_name = f'{children[0].value}.{children[1].value}'
    else:
      raise ValueError(f'unknown fq_name children: {children}')
    return Token('EXPLICIT_NAME', the_name)

  def namespaced_ident(self, children):
    ret = ''
    for i in range(0, len(children)-1):
      ret += children[i].value + '.'
    ret += children[len(children)-1].value
    return Token('EXPLICIT_NAME', ret)

# not happy to have to do this HACK, but real WDL workflows appear to require it...
# basically, in the bash command block, they can do ~{"--bash_flag "+wdl_var} where wdl_var is
# optional, and if wdl_var is empty, the whole thing is expected to resolve to empty. So we need to
# ahead of time mark such additions as the special "resolve to empty if null arg" type.
class OuterCmdVarOptionalStringAddHackTF(Transformer):
  def cmdvarplaceholder(self, children):
    inner_transformer = InnerCmdVarOptionalStringAddHackTF()
    transformed_children = [inner_transformer.transform(c) for c in children if isinstance(c, Tree)]
    return Tree('cmdvarplaceholder', transformed_children)

class InnerCmdVarOptionalStringAddHackTF(Transformer):
  def add(self, children):
    return Tree('weird_nulling_add', children)

@dataclass
class WdlSource:
  """Holds the parsed contents of one WDL source file."""
  wf_template: Optional[Tree]
  task_templates: dict
  # NOTE all the "filename" stuff when dealing with these WdlSource things is kind of a misnomer.
  # it can be filename with .wdl stripped, or it can be the import-as.
  filename: str
  is_root_wf: bool

  def __post_init__(self):
    tf_fq_name = FullyQualifiedNameTF(visit_tokens=False)
    tf_hack_cmd = OuterCmdVarOptionalStringAddHackTF(visit_tokens=False)
    if self.wf_template:
      self.wf_template = tf_fq_name.transform(self.wf_template)
    for name, task in self.task_templates.items():
      self.task_templates[name] = tf_fq_name.transform(tf_hack_cmd.transform(task))
    if self.is_root_wf:
      assert self.wf_template is not None
      wf_name = self.wf_template.children[0].value
      # doing ImpliedNameTF here, but only for root, is to address root's scatter not happening.
      # it's not great that i arrived at doing this without really understanding why, but oh well it works lol
      self.wf_template = ImpliedNameTF(wf_name).transform(self.wf_template)
      self.wf_template = RootWorkflowInputDeclsTF().transform(self.wf_template)

  def wf_name(self):
    if self.wf_template is None:
      return None
    return self.wf_template.children[0].value

class WdlSourceMap:
  def __init__(self, the_map):
    self.the_map = the_map # maps from wdl import-as names to WdlSource

  # returns (template, is_wf, wdl_src_fname) where wdl_src_fname is where template was found. Or None.
  def lookup(self, name, home_wdl_fname):
    cur_wdl = self.the_map[home_wdl_fname]
    maybe_task = cur_wdl.task_templates.get(name)
    if maybe_task:
      return maybe_task, False, home_wdl_fname
    if cur_wdl.wf_name() == name:
      return cur_wdl.wf_template, True, home_wdl_fname
    name_components = name.split('.', 1)
    foreign_name = name_components[0]
    maybe_foreign_wdl = self.the_map.get(foreign_name)
    if maybe_foreign_wdl is None:
      return None
    thing_to_call_name = name_components[1]
    maybe_task = maybe_foreign_wdl.task_templates.get(thing_to_call_name)
    if maybe_task:
      return maybe_task, False, foreign_name
    if maybe_foreign_wdl.wf_name() == thing_to_call_name:
      return maybe_foreign_wdl.wf_template, True, foreign_name
    return None

ROOT_WF_IMPORT_AS_NAME = 'WLWWDLROOTZYX'

class WDLJob:
  # file_contents_override is for testing
  def __init__(self, workflow_dir, root_filename, file_contents_override = None): # TODO could have a JobTemplate class that does this init, and is separate from JobContext (careful about needing deepcopy)
    self.temp_wdl_sources = {} # TODO might be serious memory waste here... should have a single central one everyone has a reference to.

    self.struct_field_types = {}
    struct_typenames = struct_types_parse.get_all_struct_typenames(workflow_dir, root_filename, file_contents_override)
    log_info(f'found structs: {struct_typenames}')

    grammar = wdl_grammar.productions
    if struct_typenames:
      struct_rule = 'type_is_struct: ' + ' | '.join(f'"{s}"' for s in struct_typenames)
      grammar = grammar.replace('type_is_struct: "WillowStructInstantiationZYXABC"', struct_rule)
    else:
      grammar = grammar.replace(' | type_is_struct', '')
      grammar = grammar.replace('type_is_struct: "WillowStructInstantiationZYXABC"', '')
    parser = Lark(grammar, parser='lalr')

    wdl_parser = _WdlParser(workflow_dir, parser, self.struct_field_types)
    try:
      self.temp_wdl_sources = wdl_parser.parse_main_wdl(root_filename, file_contents_override)
    except Exception as e:
      raise ValueError(f'Failed to parse WDL {root_filename}. Original exception:\n\n{e}') from e

    root_source = self.temp_wdl_sources.get(ROOT_WF_IMPORT_AS_NAME)
    if root_source is None:
      raise ValueError(f'root wdl file struct not gotten for filename {root_filename}!!!')
    if root_source.wf_template is None:
      raise ValueError(f'root wdl file ({root_filename}) does not have a "workflow" top-level block')

    self.job_context = None
    self.NEW_root_wf = None
    self.wdl_map = WdlSourceMap(self.temp_wdl_sources)

  # the ctor makes a *template* instance. this is what you call to make it into the state tracker
  # for a particular job. (meant to be called after deep-copying a template WDLJob).
  def instantiate(self, useremail, s3_bucket, job_id, user_inputs=None):
    if user_inputs is None:
      user_inputs = {}
    root_source = self.temp_wdl_sources[ROOT_WF_IMPORT_AS_NAME]
    root_wf_name = root_source.wf_template.children[0].value
    self.job_context = JobContext(job_id=job_id, useremail=useremail, s3_bucket=s3_bucket,
                                  queued_tasks_execs=[],
                                  queued_size_queries=[],
                                  struct_field_types=self.struct_field_types,
                                  resolved_variables=ResolvedVariables())
    self.NEW_root_wf = Workflow(root_wf_name, root_source.wf_template,
                                self.job_context, self.wdl_map, ROOT_WF_IMPORT_AS_NAME)
    self.NEW_root_wf.wf_tree = WorkflowUserInputsTF(user_inputs, root_wf_name).transform(self.NEW_root_wf.wf_tree)
    self.NEW_root_wf.resolve_all_possible()

  def notify_files_available(self, cloudpaths_map):
    log_debug(f'will now notify that files {list(cloudpaths_map.keys())} are available', self.job_context.job_id)
    self.job_context.resolved_variables.notify_files_available(cloudpaths_map)
    self.NEW_root_wf.notify_files_available()

  def notify_size_response(self, size_results):
    log_debug(f'will now notify that sizes for {list(size_results.keys())} are available', self.job_context.job_id)
    self.NEW_root_wf.accept_size_response(size_results)

  def notify_command_finished(self, the_json):
    task_call_as_name = the_json['task_call_as']
    if 'glob_result_lists' in the_json:
      self.NEW_root_wf.accept_glob_result_lists(task_call_as_name, the_json['glob_result_lists'])
    if 'cmd_stdout' in the_json:
      self.NEW_root_wf.accept_cmd_stdout(task_call_as_name, the_json['cmd_stdout'])
    if 'file_contents' in the_json:
      self.NEW_root_wf.accept_file_contents(task_call_as_name, the_json['file_contents'])
    # NOTE: notify_files_available() must happen after accept_glob_result_lists() beacuse the
    #       glob results leave files in "pending" state, that can only be resolved by the cloudpaths
    #       in this particular notify_files_available() call.
    if 'cloudpaths' in the_json:
      self.notify_files_available(the_json['cloudpaths'])

    self.NEW_root_wf.notify_command_finished(task_call_as_name, the_json['status'])

  def get_task_outputs(self, task_call_as_name):
    return self.NEW_root_wf.get_task_outputs(task_call_as_name)

  def notify_task_cache_hit(self, task_name, outputs):
    task = self.NEW_root_wf.find_task(task_name)
    if not task:
      log_error(f'Could not find task "{task_name}" to apply cache hit. This is unexpected.')
      return

    log_debug(f'task cache hit for {task_name}, cached outputs: {outputs}')
    for out_bare_name, out_tok in outputs.items():
      self.job_context.insert_resolved_var(f'{task_name}.{out_bare_name}', out_tok)
    self.NEW_root_wf.notify_task_cache_hit(task_name, outputs)
    self.NEW_root_wf.notify_files_available()

  def any_pending(self):
    return self.NEW_root_wf.any_task_pending()

  def all_outputs_available(self):
    root_wf_name = self.NEW_root_wf.my_wf_name
    for var in self.NEW_root_wf.my_output_names:
      res = self.job_context.resolved_variables.get(f'{root_wf_name}.{var}')
      if res is None or 'PENDING' in res.type:
        return False
    return True

  def get_root_workflow_outputs(self):
    return self.NEW_root_wf.get_workflow_outputs()

  # TODO HACK hopefully clean up eventually
  def root_called_tasks_for_test(self):
    return self.NEW_root_wf.called_tasks

  def __repr__(self):
    return repr(self.NEW_root_wf)
