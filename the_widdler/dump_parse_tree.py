import sys
from lark import Lark
from lark import Tree, Token
import wdl_grammar

# usage: ./dump_parse_tree.py file.wdl [TaskOrWorkflowNameYouCareAbout]

wanted_unit = '' if len(sys.argv) == 2 else sys.argv[2]
care_about_command_block = True

def to_str_nonterm(node, depth):
  treedump = f'{depth}{node.data}\n'
  for child in node.children:
    if type(child) == Tree:
      if child.data in ('task', 'workflow') and wanted_unit and child.children[0].value != wanted_unit:
        continue
      if child.data == 'command' and not care_about_command_block:
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
  for c in node.children:
    if type(c) == Tree and c.data == 'cmdvarplaceholder':
      vars_found += 1
    elif type(c) == Token and c.type == 'FINALIZED_STR':
      strs_found += 1
    else:
      others_found += 1
  return f'{depth}command node: {vars_found} cmdvarplaceholder children, {strs_found} FINALIZED_STR children, {others_found} other children\n'

with open(sys.argv[1], 'r', encoding='utf-8') as file:
  file_contents = file.read()
  parser = Lark(wdl_grammar.productions, parser='lalr')
  tree = parser.parse(file_contents)
  print(to_str_nonterm(tree, ''))
