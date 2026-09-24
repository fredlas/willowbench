from lark import Transformer, Tree, Token # pylint: disable=unused-import

from tf_common import CloudLocalFile, my_str_to_bool, WILLOWNULL
from util import log_error,log_warn,log_info,log_debug # pylint: disable=unused-import

# ==================================================================================================
# This section is for 'run-time' transformations. More substantial things, to be done on the
# invocation copies, not the original template.


class FileNowAvailableTF(Transformer):
  def __init__(self, job_context):
    super().__init__(visit_tokens=True)
    self.job_context = job_context

  def PENDING_FILE(self, pending_f_tok):
    wdl_file = pending_f_tok.value
    if wdl_file.originating_fqvn in self.job_context.resolved_variables.available_cloudpaths:
      wdl_file.cloud_path = self.job_context.resolved_variables.available_cloudpaths[wdl_file.originating_fqvn]
      if wdl_file.cloud_path:
        return Token('FINALIZED_FILE', wdl_file)
      return WILLOWNULL
    return pending_f_tok

  def PENDING_FILEARRAY(self, pending_arr_tok):
    file_list = pending_arr_tok.value
    all_finalized = True
    for i in range(len(file_list)): # pylint: disable=consider-using-enumerate
      if file_list[i] is None:
        continue
      if file_list[i].cloud_path is None:
        if file_list[i].originating_fqvn in self.job_context.resolved_variables.available_cloudpaths:
          maybe_cloudpath = self.job_context.resolved_variables.available_cloudpaths[file_list[i].originating_fqvn]
          if maybe_cloudpath == '':
            file_list[i] = None
          else:
            file_list[i].cloud_path = maybe_cloudpath
        else:
          all_finalized = False
    if all_finalized:
      return Token('FINALIZED_FILEARRAY', file_list)
    return pending_arr_tok

def contents_to_wdl_type(contents, wdl_type):
  if wdl_type == 'read_string':
    return Token('FINALIZED_STR', contents)
  if wdl_type == 'read_int':
    return Token('FINALIZED_INT', int(contents))
  if wdl_type == 'read_float':
    return Token('FINALIZED_FLOAT', float(contents))
  if wdl_type == 'read_boolean':
    return Token('FINALIZED_BOOLEAN', my_str_to_bool(contents))
  if wdl_type == 'read_tsv':
    rows = []
    lines = contents.splitlines()
    for i, line in enumerate(lines):
      if i == len(lines) - 1 and not line:
        continue # Skip appending for the very last empty line
      rows.append(Token('FINALIZED_ARRAY', line.split('\t')))
    return Token('FINALIZED_ARRAY', rows)
  raise ValueError(f'unknown type in contents_to_wdl_type: {wdl_type}')

class StdoutNowAvailableTF(Transformer):
  def __init__(self, call_as_name, stdout_str):
    super().__init__(visit_tokens=True)
    self.call_as_name = call_as_name
    self.stdout_str = stdout_str

  def PENDING_STDOUT(self, node):
    assert node.value == self.call_as_name
    return Token('FINALIZED_STR', self.stdout_str)

  def PENDING_READ_FROM_STDOUT(self, node):
    return contents_to_wdl_type(self.stdout_str, node.value)


class FileContentsNowAvailableTF(Transformer):
  def __init__(self, file_contents):
    super().__init__(visit_tokens=True)
    self.file_contents = file_contents

  def PENDING_READ_FROM_FILE(self, node):
    out_type, fname = node.value
    return contents_to_wdl_type(self.file_contents[fname], out_type)


class GlobResultNowAvailableTF(Transformer):
  def __init__(self, glob_lists):
    super().__init__(visit_tokens=True)
    self.glob_lists = glob_lists

  def PENDING_GLOB(self, node):
    our_list = self.glob_lists[node.value]
    arr = [CloudLocalFile(var_name=f'WLLWglobbed_{node.value}_IDX{i}',
                          originating_fqvn=f'WLLWglobbed_{node.value}_IDX{i}',
                          cloud_path=cloudpath) for i, cloudpath in enumerate(our_list)]
    return Token('PENDING_FILEARRAY', arr)


class SizeNowAvailableTF(Transformer):
  def __init__(self, size_results):
    super().__init__(visit_tokens=True)
    self.size_results = {k: int(v) for k, v in size_results.items()}

  def PENDING_SIZE(self, tok):
    cloud_paths, unit = tok.value

    if not all(p in self.size_results for p in cloud_paths):
        return tok # Not all sizes are available yet

    total_size_bytes = sum(self.size_results[p] for p in cloud_paths)

    unit_map = {
        "B": 1,
        "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12,
        "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12,
        "KiB": 2**10, "MiB": 2**20, "GiB": 2**30, "TiB": 2**40,
        "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40,
    }

    if unit not in unit_map:
        raise ValueError(f"Unsupported unit for size(): {unit}")

    scaler = unit_map[unit]
    return Token('FINALIZED_FLOAT', float(total_size_bytes) / scaler)


class CommandFinishedTF(Transformer):
  def __init__(self, task_name, status):
    super().__init__(visit_tokens=True)
    self.task_name = task_name
    self.status = status

  def RUNNING_CMD(self, _node):
    if self.status == 'OK':
      return Token('SUCCEEDED_CMD', 'OK')
    return Token('FAILED_CMD', self.status)
