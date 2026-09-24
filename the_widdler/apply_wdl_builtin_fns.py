import math
from lark import Tree, Token

from tf_common import WILLOWNULL, is_resolved, token_from_finalized_arr_index

def _is_read_stdout(children):
    if type(children[0].value) != str: # TODO TRIM
        raise ValueError(f'wait wtf, apply_wdl_builtin_fn children[0] isnt a string??? its {children[0].value}, and children is {children}')
    return children[0].value.startswith('read_') and (type(children[1]) is Token and children[1].type == 'PENDING_STDOUT' or type(children[1]) is Tree and children[1].data == 'apply_wdl_fn' and children[1].children[0].value == 'stdout')

def _do_wdl_select_first(args):
    assert type(args[0].value) is list
    for i, x in enumerate(args[0].value):
        if x is not None:
            return token_from_finalized_arr_index(args[0], i)
    return WILLOWNULL

def _do_wdl_defined(args):
    return Token('FINALIZED_BOOLEAN', args[0].value is not None)

def _do_wdl_basename(args):
    file_arg = args[0]
    if file_arg.type == 'WILLOWNULL':
        return Token('FINALIZED_STR', '')
    if 'FILE' not in file_arg.type:
        raise ValueError(f'basename() function expects a File argument, got {file_arg.type if file_arg else "None"}')
    if 'ARRAY' in file_arg.type:
        raise ValueError('basename() function does not operate on arrays of Files')

    path = file_arg.value.cloud_path
    basename = path.split('/')[-1]

    if len(args) > 1 and args[1] is not None and args[1].type != 'WILLOWNULL':
        suffix = args[1].value
        if basename.endswith(suffix):
            basename = basename[:-len(suffix)]
    return Token('FINALIZED_STR', basename)

# (assumes the child is not a stdout())
def _do_wdl_read_file(tf, children):
    the_type = children[0].value
    the_arg = children[1]
    # if not stdout(), then the arg is supposed to be a filename. if it's an unresolved WDL String,
    # we should wait for it to get resolved... except we need to block the command from running while
    # we're waiting, and that's going to take a whole extra new piece of machinery, so for now let's
    # only support string literals (that's all I've seen so far anyways)
    if not (type(the_arg) is Token and the_arg.type == 'FINALIZED_STR'): # literal filename
        raise ValueError(f'only string literal filenames and stdout() are accepted by {the_type}(). It looks like you passed a String var. This will need some work to support.')
    tf.file_contents_requests.add(the_arg.value)
    if the_type == 'read_tsv':
      if len(children) > 2:
        if children[2].value is not False:
          raise ValueError('treat-first-line-as-header not yet supported in read_tsv()')
    return Token('PENDING_READ_FROM_FILE', (the_type, the_arg.value))

def _do_wdl_glob(tf, args):
    assert args[0].type == 'FINALIZED_STR'
    tf.glob_requests.add(args[0].value)
    return Token('PENDING_GLOB', args[0].value)

def _do_wdl_size(tf, children):
    file_arg = children[1]
    if file_arg.type == 'WILLOWNULL':
        return Token('FINALIZED_FLOAT', 0.0)
    if 'FILE' not in file_arg.type:
        raise ValueError(f'size() function expects a File or Array[File] argument, got {file_arg.type if file_arg else "None"}')

    if ('ARRAY' not in file_arg.type and file_arg.value.cloud_path is None) or ('ARRAY' in file_arg.type and any(x.cloud_path is None for x in file_arg.value)):
        return Tree('apply_wdl_fn', children) # can't query size of files whose cloudpaths we don't know!
    cloud_paths = [f.cloud_path for f in file_arg.value] if 'ARRAY' in file_arg.type else [file_arg.value.cloud_path]
    tf.job_context.queued_size_queries.append(cloud_paths)

    unit = "B"
    if len(children) > 2 and children[2] is not None and children[2].type != 'WILLOWNULL':
        unit = children[2].value
    return Token('PENDING_SIZE', (cloud_paths, unit))

def _do_wdl_sum(args):
    total_sum = 0
    for item in args:
        if isinstance(item.value, list):
            for hack_hack in item.value:
                if isinstance(hack_hack, list): # TODO TODO HACK HACK
                    total_sum += sum(hack_hack)
                else:
                    total_sum += hack_hack
        else:
            total_sum += item.value
    if isinstance(total_sum, int):
        return Token('FINALIZED_INT', total_sum)
    if isinstance(total_sum, float):
        return Token('FINALIZED_FLOAT', total_sum)
    raise ValueError(f'sum result is {type(total_sum)}, expected int or float')

def apply_wdl_builtin_fn(tf, children):
    if _is_read_stdout(children):
        # HACK ugh, wish this (i.e. e.g. read_int(stdout()) ) could be handled normally, but normal
        # handling would have us wait until the stdout() is resolved, but then it would resolve into a
        # string and we wouldn't know to treat it as stdout contents rather than a filename.
        tf.stdout_requested = True
        return Token('PENDING_READ_FROM_STDOUT', children[0].value)
    which_fn = children[0].value

    if any(x is not None and not is_resolved(x) for x in children[1:]):
        return Tree('apply_wdl_fn', children)

    if which_fn.startswith('read_'):
        return _do_wdl_read_file(tf, children)
    elif which_fn == 'select_first':
        return _do_wdl_select_first(children[1:])
    elif which_fn == 'defined':
        return _do_wdl_defined(children[1:])
    elif which_fn == 'stdout':
        tf.stdout_requested = True
        return Token('PENDING_STDOUT', tf.unitname)
    elif which_fn == 'glob':
        return _do_wdl_glob(tf, children[1:])
    elif which_fn == 'size':
        return _do_wdl_size(tf, children)
    elif which_fn == 'ceil':
        return Token('FINALIZED_INT', math.ceil(children[1].value))
    elif which_fn == 'floor':
        return Token('FINALIZED_INT', math.floor(children[1].value))
    elif which_fn == 'basename':
        return _do_wdl_basename(children[1:])
    elif which_fn == 'sum':
        return _do_wdl_sum(children[1:])
    elif which_fn == 'length':
        return Token('FINALIZED_INT', len(children[1].value))
    elif which_fn == 'range':
        return Token('FINALIZED_ARRAY', list(range(children[1].value)))
    else:
        raise ValueError(f'unknown WDL built-in function: "{which_fn}()", children: {children}')
