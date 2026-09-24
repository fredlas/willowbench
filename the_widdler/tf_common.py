from lark import Token
from dataclasses import dataclass
from typing import Optional

WILLOWNULL = Token('WILLOWNULL', None)

NUMERIC_TYPES = ['FINALIZED_INT', 'FINALIZED_FLOAT']
FINALIZED_TYPES = ['FINALIZED_STR', 'FINALIZED_INT', 'FINALIZED_FLOAT', 'FINALIZED_ARRAY',
                   'FINALIZED_FILEARRAY', 'FINALIZED_BOOLEAN', 'FINALIZED_FILE',
                   'PROPAGATABLE_STRUCT', 'WILLOWNULL']
def is_resolved(node):
  return type(node) is Token and node.type in FINALIZED_TYPES

# is_resolved but also allow PENDING_FILE... basically the idea is, it's ok to do the decl
# propagation stuff, but don't go around finalizing commands to be run.
def is_propagatable(node):
  return type(node) is Token and (node.type in FINALIZED_TYPES or node.type in ('PENDING_FILE', 'PENDING_FILEARRAY'))

# NOTE on how files work. Once inputs have been "accepted" (which includes the cloud path the file
# inputs can eventually be found at), File types are transformed to PENDING_FILE, meaning not yet
# available. Those become FINALIZED_FILE once they are available for download from cloud.
@dataclass
class CloudLocalFile:
    """Represents a WDL File type, capturing its WDL variable name, cloud path and finalization status."""
    var_name: str
    # the fully-qualified var name of the task input, or workflow user input, where this File originated
    originating_fqvn: str
    # for a finalized file, this is its cloud URL. for a pending file, this is None.
    cloud_path: Optional[str] = None
    def __repr__(self):
      return f'(warning, this CloudLocalFile, with var_name {self.var_name}, originating_fqvn {self.originating_fqvn}, has empty cloud_path)' if self.cloud_path is None else self.cloud_path

# Tree('var_decl_thing') always has two children. The first is always Tree('decl_meta'), with Token
# children DECL_TYPE, DECL_NAME, OPTIONALITY. The second is the thing that the decl has been filled
# with, which should only happen once (although maybe that thing might get further resolved later...
# just saying it won't get re-assigned).
#
# For these accessors, pass the array of children - e.g. `c` in `foo(self, c)`
def decl_type(decl_children): # should be File, ArrayFile, String, Boolean, Struct, etc
  return decl_children[0].children[0].value
def decl_name(decl_children):
  return decl_children[0].children[1].value
def decl_is_optional(decl_children):
  return decl_children[0].children[2].value
def decl_rhs(decl_children):
  return decl_children[1]

def decl_meta_type(decl_meta_node): # should be File, ArrayFile, String, Boolean, Struct, etc
  return decl_meta_node.children[0].value
def decl_meta_name(decl_meta_node):
  return decl_meta_node.children[1].value
def decl_meta_is_optional(decl_meta_node):
  return decl_meta_node.children[2].value
def decl_meta_unpack(decl_meta_node):
  return decl_meta_type(decl_meta_node), decl_meta_name(decl_meta_node), decl_meta_is_optional(decl_meta_node)

def token_from_python_prim(thing):
  if isinstance(thing, int):
    return Token('FINALIZED_INT', thing)
  if isinstance(thing, str):
    return Token('FINALIZED_STR', thing)
  if isinstance(thing, float):
    return Token('FINALIZED_FLOAT', thing)
  if isinstance(thing, bool):
    return Token('FINALIZED_BOOLEAN', thing)
  if isinstance(thing, list):
    return Token('FINALIZED_ARRAY', thing)
  if thing is None:
    return WILLOWNULL
  raise ValueError(f'unhandled python type: {type(thing)}')

def token_from_finalized_arr_index(arr_node, the_ind):
  if arr_node.type == 'FINALIZED_ARRAY':
    if type(arr_node.value[the_ind]) is Token and arr_node.value[the_ind].type == 'FINALIZED_ARRAY':
      return arr_node.value[the_ind] # matrix outer index access; value is already a Token
    return token_from_python_prim(arr_node.value[the_ind])
  if arr_node.type == 'FINALIZED_FILEARRAY':
    if arr_node.value[the_ind] is None:
      return WILLOWNULL
    return Token('FINALIZED_FILE', arr_node.value[the_ind])
  if arr_node.type == 'PENDING_FILEARRAY':
    if arr_node.value[the_ind] is None:
      return WILLOWNULL
    wdl_file = arr_node.value[the_ind]
    token_type = 'FINALIZED_FILE' if (wdl_file.cloud_path is not None) else 'PENDING_FILE'
    return Token(token_type, wdl_file)
  raise ValueError(f'expected type FINALIZED_ARRAY or *FILEARRAY, got {arr_node.type}') from None

def indexed_array_name(array_varname, i):
  return f'{array_varname}_WILLOWindex{i}'

def my_str_to_bool(the_str):
  return the_str[0] in {'T', 't', 'Y', 'y'}
