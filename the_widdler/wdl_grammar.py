productions = r"""
// document

start: version? document_element*

version: "version" /[^ \t\r\n]+/
import_alias: "alias" CNAME "as" CNAME
import_doc: "import" string_literal ["as" CNAME] import_alias*

// workflow

workflow: "workflow" CNAME "{" workflow_element* "}"
?workflow_element: input_decls | call | scatter | conditional | workflow_outputs | meta_section | any_decl

scatter: "scatter" "(" CNAME "in" expr ")" "{" inner_workflow_element* "}"
conditional: "if" "(" expr ")" "{" inner_workflow_element* "}"
?inner_workflow_element: any_decl | call | scatter | conditional

call: "call" namespaced_ident _call_body? -> call
    | "call" namespaced_ident "as" CNAME _call_body? -> call_as
namespaced_ident: CNAME ("." CNAME)*
call_inputs: "input" ":" [call_input ("," call_input)*] ","?
_call_body: "{" call_inputs? "}"
call_input: CNAME "=" expr


// task


task: "task" CNAME "{" task_section* command task_section* "}"
?task_section: input_decls
             | output_decls
             | meta_section
             | runtime_section
             | any_decl -> noninput_decl

tasks: task*

input_decls: "input" "{" any_decl* "}"
output_decls: "output" "{" bound_decl* "}"

// WDL task commands: with {} and <<< >>> command and ${} and ~{} placeholder styles
!?placeholder_key: "default" | "false" | "true" | "sep"
?placeholder_value: string_literal
                  | INT -> int
                  | FLOAT -> float
placeholder_option: placeholder_key "=" placeholder_value
cmdvarplaceholder: placeholder_option* expr


?command: command1 | command2

// meta/parameter_meta sections (effectively JSON)
meta_object: "{" [meta_kv (","? meta_kv)*] "}"
meta_kv: CNAME ":" meta_value
?meta_value: literal | string_literal
           | meta_object
           | "[" [meta_value ("," meta_value)*] "]" -> meta_array
!meta_section: ("meta" | "parameter_meta") meta_object

// task runtime section (key-expression pairs)
runtime_section: "runtime" "{" [runtime_kv (","? runtime_kv)*] "}"
runtime_kv: CNAME ":" expr


// decl


unbound_decl: type CNAME -> decl
bound_decl: type CNAME "=" expr -> decl
?any_decl: unbound_decl | bound_decl


// type


_quant: optional | nonempty | optional_nonempty
optional: "?"
nonempty: "+"
optional_nonempty: "+?"


CNAME: /[a-zA-Z][a-zA-Z0-9_]*/
COMMENT: /[ \t]*/ "#" /[^\r\n]*/
SPACE: /[ \t]+/

%import common.INT
%import common.SIGNED_INT
%import common.FLOAT
%import common.SIGNED_FLOAT
%import common.ESCAPED_STRING
%import common.NEWLINE
%ignore SPACE
%ignore NEWLINE
%ignore COMMENT


// expr


?expr: expr_infix

?expr_infix: expr_infix0

?expr_infix0: expr_infix0 "||" expr_infix1 -> lor
            | expr_infix1

?expr_infix1: expr_infix1 "&&" expr_infix2 -> land
            | expr_infix2

?expr_infix2: expr_infix2 "==" expr_infix3 -> eqeq
            | expr_infix2 "!=" expr_infix3 -> neq
            | expr_infix2 "<=" expr_infix3 -> lte
            | expr_infix2 ">=" expr_infix3 -> gte
            | expr_infix2 "<" expr_infix3 -> lt
            | expr_infix2 ">" expr_infix3 -> gt
            | expr_infix3

?expr_infix3: expr_infix3 "+" expr_infix4 -> add
            | expr_infix3 "-" expr_infix4 -> sub
            | expr_infix4

?expr_infix4: expr_infix4 "*" expr_infix5 -> mul
            | expr_infix4 "/" expr_infix5 -> div
            | expr_infix4 "%" expr_infix5 -> rem
            | expr_infix5

?expr_infix5: expr_core

?literal: "true"-> boolean_true
        | "false" -> boolean_false
        | INT -> int
        | SIGNED_INT -> int
        | FLOAT -> float
        | SIGNED_FLOAT -> float

?string: string1 | string2

_DOUBLE_BACKSLASH.2: "\\\\"
STRING_INNER1: (_DOUBLE_BACKSLASH|"\\'"|/[^']/)
ESCAPED_STRING1: "'" STRING_INNER1* "'"
string_literal: ESCAPED_STRING | ESCAPED_STRING1

?map_key: expr_core
map_kv: map_key ":" expr

// expression core (everything but infix)
// we stuck this last down here so that further language-version-specific
// productions can be added below
?expr_core: "(" expr ")"
          | literal
          | string
          | "!" expr_core -> negate

          | "[" [expr ("," expr)*] ","? "]" -> array
          | expr_core "[" expr "]" -> at

          | "(" expr "," expr ")" -> pair
          | "{" [map_kv ("," map_kv)*] ","? "}" -> map

          | "if" expr "then" expr "else" expr -> ifthenelse

          | CNAME "(" [expr ("," expr)*] ")" -> apply_wdl_fn

          | CNAME -> left_name
          | expr_core "." CNAME -> fq_name
          | "object" "{" [object_kv ("," object_kv)* ","?] "}" -> obj // appends to expr_core

object_kv:  CNAME ":" expr
          | string_literal ":" expr

// TODO for now not supporting nested types. Only Array[prim], Map[prim, prim], Pair[prim, prim]. and now also Array[Array[NonString]].
// WDL types
// (compound types Array[type], Map[type, type], and Pair[type, type] are all possible)
// (individual types "String" | "Float" | "Int" | "Boolean" | "File" are all possible)
type: type_noncompound _quant?
    | type_is_matrix "[" type_noncompound "]" _quant?
    | type_is_array "[" type_noncompound "]" _quant?
    | type_is_map "[" type_noncompound "," type_noncompound "]" _quant?
    | type_is_pair "[" type_noncompound "," type_noncompound "]" _quant?


type_noncompound: type_is_string | type_is_float | type_is_int | type_is_bool | type_is_file | type_is_struct
type_is_string: "String"
type_is_float: "Float"
type_is_int: "Int"
type_is_bool: "Boolean"
type_is_file: "File"
type_is_struct: "WillowStructInstantiationZYXABC"
type_is_array: "Array"
type_is_matrix: "Matrix"
type_is_map: "Map"
type_is_pair: "Pair"

_EITHER_DELIM.2: "~{" | "${"

// string (single-quoted)
STRING1_CHAR: _DOUBLE_BACKSLASH | "\\'" | /[^'~$]/ | /\$(?=[^{])/ | /\~(?=[^{])/
STRING1_FRAGMENT: STRING1_CHAR+
string1: /'/ (STRING1_FRAGMENT? _EITHER_DELIM expr "}")* STRING1_FRAGMENT? /'/ -> string

// string (double-quoted)
STRING2_CHAR: _DOUBLE_BACKSLASH | "\\\"" | /[^"~$]/ | /\$(?=[^{])/ | /~(?=[^{])/
STRING2_FRAGMENT: STRING2_CHAR+
string2: /"/ (STRING2_FRAGMENT? _EITHER_DELIM expr "}")* STRING2_FRAGMENT? /"/ -> string

COMMAND1_CHAR: /[^~$}]/ | /\$(?=[^{])/ | /~(?=[^{])/
COMMAND1_FRAGMENT: COMMAND1_CHAR+
command1: "command" "{" (COMMAND1_FRAGMENT? _EITHER_DELIM cmdvarplaceholder "}")* COMMAND1_FRAGMENT? "}" -> command

COMMAND2_CHAR: /[^~>]/ | /~(?=[^{])/ | />(?=[^>])/ | />>(?=[^>])/
COMMAND2_FRAGMENT: COMMAND2_CHAR+
command2: "command" "<<<" (COMMAND2_FRAGMENT? "~{" cmdvarplaceholder "}")* COMMAND2_FRAGMENT? ">>>" -> command

?workflow_outputs: output_decls

// struct definitions
struct: "struct" CNAME "{" unbound_decl* "}"

?document_element: import_doc | task | workflow | struct
"""
