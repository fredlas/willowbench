import unittest
import base64
import zlib
import sys
from WDLJob import WDLJob, JobContext, ResolvedVariables
from tf_common import WILLOWNULL
import lark
from lark import Token
from tf_resolution import ResolutionTF

def supress_logs(_a,_b,_c):
  pass
import util
if '-v' not in sys.argv:
    util.log_common = supress_logs
else:
    sys.argv.remove('-v') # Remove it so unittest.main() doesn't complain

class TestResolutionTFExpressions(unittest.TestCase):
  def test_add_integers(self):
    tf = ResolutionTF('test', JobContext('testjob', 'test', 'test', [], [], {}, ResolvedVariables()))
    left = Token('FINALIZED_INT', 2)
    right = Token('FINALIZED_INT', 3)
    result = tf.add([left, right])
    self.assertEqual(result.type, 'FINALIZED_INT')
    self.assertEqual(result.value, 5)

  def test_add_float_and_int(self):
    tf = ResolutionTF('test', JobContext('testjob', 'test', 'test', [], [], {}, ResolvedVariables()))
    left = Token('FINALIZED_INT', 2)
    right = Token('FINALIZED_FLOAT', 3.5)
    result = tf.add([left, right])
    self.assertEqual(result.type, 'FINALIZED_FLOAT')
    self.assertAlmostEqual(result.value, 5.5)

  def test_sum(self):
    tf = ResolutionTF('test', JobContext('testjob', 'test', 'test', [], [], {}, ResolvedVariables()))
    children = [Token('whatevs', 'sum'),
                Token('FINALIZED_ARRAY', [1,2,3]),
                Token('FINALIZED_INT', 4),
                Token('FINALIZED_ARRAY', [5, 6.6])]
    result = tf.apply_wdl_fn(children)
    self.assertEqual(result.type, 'FINALIZED_FLOAT')
    self.assertEqual(result.value, 21.6)

  def test_compare_integers(self):
    tf = ResolutionTF('test', JobContext('testjob', 'test', 'test', [], [], {}, ResolvedVariables()))
    left = Token('FINALIZED_INT', 5)
    right = Token('FINALIZED_INT', 3)
    result = tf.gt([left, right])
    self.assertEqual(result.type, 'FINALIZED_BOOLEAN')
    self.assertTrue(result.value)

  def test_array_access(self):
    tf = ResolutionTF('test', JobContext('testjob', 'test', 'test', [], [], {}, ResolvedVariables()))
    arr_token = Token('FINALIZED_ARRAY', [10, 20, 30])
    index_token = Token('FINALIZED_INT', 1)
    result = tf.at([arr_token, index_token])
    self.assertEqual(result.type, 'FINALIZED_INT')
    self.assertEqual(result.value, 20)

  def test_both_wdl_command_styles(self):
    wdl_content = '''
        workflow wf {
          call Meet  { input: name = "World" }
          call Greet { input: name = "World" }
        }
        task Meet {
          input { String name }
          command {
            echo "Hello, ~{name}!"
          }
        }
        task Greet {
          input { String name }
          command <<<
            echo "Hello, ~{name}!"
          >>>
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate('test', 'test', 'testjob')
    command0 = zlib.decompress(base64.b64decode(job.job_context.queued_tasks_execs[0]['bash_cmds'])).decode('utf-8')
    command1 = zlib.decompress(base64.b64decode(job.job_context.queued_tasks_execs[1]['bash_cmds'])).decode('utf-8')
    self.assertTrue('echo "Hello, World!"' in command0)
    self.assertTrue('echo "Hello, World!"' in command1)

  def test_ceil_floor(self):
    wdl_content = '''
        workflow wf { output { Int the_ceil = ceil(0.5)
                               Int the_floor = floor(0.5) } }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate('test', 'test', 'testjob')
    outputs = job.get_root_workflow_outputs()
    self.assertEqual(outputs['the_ceil'], 1)
    self.assertEqual(outputs['the_floor'], 0)

  def test_string_interpolation(self):
    wdl_content = '''
        workflow wf {
          input { String who }
          call Greet {
            input: name = who
          }
        }
        task Greet {
          input {
            String name
          }
          command <<<
            echo "Hello, ~{name}!"
          >>>
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate('test', 'test', 'testjob', user_inputs={'who': 'World'})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    task_info = job.job_context.queued_tasks_execs[0]
    command = zlib.decompress(base64.b64decode(task_info['bash_cmds'])).decode('utf-8')
    self.assertTrue('echo "Hello, World!"' in command)

  def test_fancy_interpolation(self):
    wdl_content = '''
        version 1.0
        workflow TheWF { call TheTask }
        task TheTask {
          input { String? will_be_empty }
          String s = "hello"
          Int i = 123
          Float f = 4.56
          Boolean b = false
          Array[String] r = ["hi", "bye"]
          command <<<
            echo ~{s} ~{i} ~{f} ~{true="bool was true" false="bool was false" b} ~{sep=" " r} ~{sep="," r} ~{default="here is something" will_be_empty}
          >>>
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate('test', 'test', 'testjob')
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    task_info = job.job_context.queued_tasks_execs[0]
    command = zlib.decompress(base64.b64decode(task_info['bash_cmds'])).decode('utf-8')
    self.assertTrue('hello' in command)
    self.assertTrue('123' in command)
    self.assertTrue('4.56' in command)
    self.assertTrue('bool was false' in command)
    self.assertTrue('hi bye' in command)
    self.assertTrue('hi,bye' in command)
    self.assertTrue('here is something' in command)

  def test_can_propagate_willownull(self):
    wdl_content = '''
        version 1.0
        workflow TheWF { call Task1 { input: s1 = "present" }
                         call Task2 { input: s1 = Task1.o1, s2 = Task1.o2 } }
        task Task1 {
          input { String? s1 String? s2 }
          command <<<
            echo ~{default="default1" s1} ~{default="default2" s2}
          >>>
          output { String? o1 = s1
                   String? o2 = s2 }
        }
        task Task2 {
          input { String? s1 String? s2 }
          command <<<
            echo ~{default="default1" s1} ~{default="default2" s2}
          >>>
          output { String o1 = s1
                   String? o2 = s2 }
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate('test', 'test', 'testjob')
    self.assertEqual(len(job.job_context.queued_tasks_execs), 2)
    task_info0 = job.job_context.queued_tasks_execs[0]
    task_info1 = job.job_context.queued_tasks_execs[1]
    command0 = zlib.decompress(base64.b64decode(task_info0['bash_cmds'])).decode('utf-8')
    command1 = zlib.decompress(base64.b64decode(task_info1['bash_cmds'])).decode('utf-8')
    self.assertTrue('present' in command0)
    self.assertFalse('default1' in command0)
    self.assertTrue('default2' in command0)
    self.assertTrue('present' in command1)
    self.assertFalse('default1' in command1)
    self.assertTrue('default2' in command1)

  def test_nonopt_detects_willownull(self):
    with self.assertRaisesRegex(ValueError, r'TheWF\.Task2\.o2 received null/None value'):
      wdl_content = '''
        version 1.0
        workflow TheWF { call Task1 { input: s1 = "present" }
                         call Task2 { input: s1 = Task1.o1, s2 = Task1.o2 } }
        task Task1 {
          input { String? s1 String? s2 }
          command <<<
            echo ~{default="default1" s1} ~{default="default2" s2}
          >>>
          output { String? o1 = s1
                   String? o2 = s2 }
        }
        task Task2 {
          input { String? s1 String? s2 }
          command <<<
            echo ~{default="default1" s1} ~{default="default2" s2}
          >>>
          output { String o1 = s1
                   String o2 = s2 }
        }
        '''
      job = WDLJob('dummy', 'dummy', wdl_content)
      job.instantiate('test', 'test', 'testjob')
      self.assertEqual(len(job.job_context.queued_tasks_execs), 2)
      task_info0 = job.job_context.queued_tasks_execs[0]
      task_info1 = job.job_context.queued_tasks_execs[1]
      command0 = zlib.decompress(base64.b64decode(task_info0['bash_cmds'])).decode('utf-8')
      command1 = zlib.decompress(base64.b64decode(task_info1['bash_cmds'])).decode('utf-8')
      self.assertTrue('present' in command0)
      self.assertFalse('default1' in command0)
      self.assertTrue('default2' in command0)
      self.assertTrue('present' in command1)
      self.assertFalse('default1' in command1)
      self.assertTrue('default2' in command1)

  def test_wdl_fn_select_first(self):
    tf = ResolutionTF('test', JobContext('testjob', 'test', 'test', [], [], {}, ResolvedVariables()))
    result = tf.apply_wdl_fn([Token('CNAME', 'select_first'), Token('FINALIZED_ARRAY', [None, 5, 2])])
    self.assertEqual(result.type, 'FINALIZED_INT')
    self.assertEqual(result.value, 5)

  def test_wdl_fn_defined(self):
    tf = ResolutionTF('test', JobContext('testjob', 'test', 'test', [], [], {}, ResolvedVariables()))
    result = tf.apply_wdl_fn([Token('CNAME', 'defined'), Token('FINALIZED_ARRAY', [None, 5, 2])])
    self.assertEqual(result.type, 'FINALIZED_BOOLEAN')
    self.assertEqual(result.value, True)
    result = tf.apply_wdl_fn([Token('CNAME', 'defined'), WILLOWNULL])
    self.assertEqual(result.type, 'FINALIZED_BOOLEAN')
    self.assertEqual(result.value, False)

CONDITIONAL_WDL_CONTENT = '''
version 1.0
workflow wf {
  input { Boolean flag }
  if (flag) {
    call echo { input: msg="hello" }
  }
}
task echo {
  input { String msg }
  command <<<
    echo $msg
  >>>
}
'''

CONDITIONAL_ON_OUTPUT_WDL = '''
workflow wf {
  input { Boolean should_do_second_task }
  call check { input: should_do_second_task=should_do_second_task }
  if (check.proceed) {
    call echo { input: msg="hello" }
  }
}
task check {
  input { Boolean should_do_second_task }
  command <<< >>>
  output { Boolean proceed = should_do_second_task }
}
task echo {
  input { String msg }
  command <<< echo ~{msg} >>>
}
'''

class TestWDLConditional(unittest.TestCase):
  def test_conditional_task(self):
    job = WDLJob('dummy', 'dummy', CONDITIONAL_WDL_CONTENT)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'flag': True})
    self.assertIn('wf.echo', job.root_called_tasks_for_test())

  def test_conditional_skipped(self):
    job = WDLJob('dummy', 'dummy', CONDITIONAL_WDL_CONTENT)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'flag': False})
    self.assertNotIn('wf.echo', job.root_called_tasks_for_test())

  def test_conditional_literal_skipped(self):
    wdl_content = '''
        workflow wf {
          if (false) {
            call echo
          }
        }
        task echo { command <<< echo yes i ran >>> }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob')
    self.assertNotIn('wf.echo', job.root_called_tasks_for_test())

  def test_conditional_literal_ran(self):
    wdl_content = '''
        workflow wf {
          if (true) {
            call echo
          }
        }
        task echo { command <<< echo yes i ran >>> }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob')
    self.assertEqual('wf.echo', job.job_context.queued_tasks_execs[0]['task_name'])

  def test_conditional_skips_on_false_task_output(self):
    job = WDLJob('dummy', 'dummy', CONDITIONAL_ON_OUTPUT_WDL)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'should_do_second_task': False})
    # 'echo' task should not run, since 'check' returns false
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    self.assertEqual(job.job_context.queued_tasks_execs[0]['task_name'], 'wf.check')

  def test_conditional_runs_on_true_task_output(self):
    job = WDLJob('dummy', 'dummy', CONDITIONAL_ON_OUTPUT_WDL)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'should_do_second_task': True})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 2)
    self.assertEqual(job.job_context.queued_tasks_execs[0]['task_name'], 'wf.check')
    self.assertEqual(job.job_context.queued_tasks_execs[1]['task_name'], 'wf.echo')

  def test_conditional_string_non_empty(self):
    wdl_content = '''
        workflow wf {
          input { String flag }
          if (flag) {
            call echo
          }
        }
        task echo { command <<< echo "ran" >>> }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'flag': 'non-empty'})
    self.assertIn('wf.echo', job.root_called_tasks_for_test())

  def test_conditional_string_empty(self):
    wdl_content = '''
        workflow wf {
          input { String flag }
          if (flag) {
            call echo
          }
        }
        task echo { command <<< echo "ran" >>> }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'flag': ''})
    self.assertNotIn('wf.echo', job.root_called_tasks_for_test())

  def test_conditional_int_non_zero(self):
    wdl_content = '''
        workflow wf {
          input { Int flag }
          if (flag) {
            call echo
          }
        }
        task echo { command <<< echo "ran" >>> }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'flag': 42})
    self.assertIn('wf.echo', job.root_called_tasks_for_test())

  def test_conditional_int_zero(self):
    wdl_content = '''
        workflow wf {
          input { Int flag }
          if (flag) {
            call echo
          }
        }
        task echo { command <<< echo "ran" >>> }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'flag': 0})
    self.assertNotIn('wf.echo', job.root_called_tasks_for_test())

  def test_conditional_float_non_zero(self):
    wdl_content = '''
        workflow wf {
          input { Float flag }
          if (flag) {
            call echo
          }
        }
        task echo { command <<< echo "ran" >>> }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'flag': 0.1})
    self.assertIn('wf.echo', job.root_called_tasks_for_test())

  def test_conditional_float_zero(self):
    wdl_content = '''
        workflow wf {
          input { Float flag }
          if (flag) {
            call echo
          }
        }
        task echo { command <<< echo "ran" >>> }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'flag': 0.0})
    self.assertNotIn('wf.echo', job.root_called_tasks_for_test())

  def test_conditional_array_non_empty(self):
    wdl_content = '''
        workflow wf {
          input { Array[Int] flag }
          if (flag) {
            call echo
          }
        }
        task echo { command <<< echo "ran" >>> }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'flag': [1,2]})
    self.assertIn('wf.echo', job.root_called_tasks_for_test())

  def test_conditional_array_empty(self):
    wdl_content = '''
        workflow wf {
          input { Array[Int] flag }
          if (flag) {
            call echo
          }
        }
        task echo { command <<< echo "ran" >>> }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'flag': []})
    self.assertNotIn('wf.echo', job.root_called_tasks_for_test())

SCATTER_WDL_CONTENT = '''
workflow wf {
  input { Array[Int] nums }
  scatter (num in nums) {
    call square { input: x=num }
  }
  output { Array[Int] results = square.result }
}
task square {
  input { Int x }
  command <<<
    echo $((x * x))
  >>>
  output { Int result = x * x }
}
'''
class TestWDLScatter(unittest.TestCase):
  def test_scatter_tasks_created(self):
    job = WDLJob('dummy', 'dummy', SCATTER_WDL_CONTENT)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'nums': [1, 2, 3]})
    self.assertEqual(len(job.root_called_tasks_for_test()), 3)
    self.assertIn('wf.square_WLWSCTRnum_0', job.root_called_tasks_for_test())
    self.assertIn('wf.square_WLWSCTRnum_1', job.root_called_tasks_for_test())
    self.assertIn('wf.square_WLWSCTRnum_2', job.root_called_tasks_for_test())

  def test_gather_outputs(self):
    job = WDLJob('dummy', 'dummy', SCATTER_WDL_CONTENT)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'nums': [1, 2, 3]})
    self.assertEqual(job.job_context.queued_tasks_execs[0]['task_name'], 'wf.square_WLWSCTRnum_0')
    self.assertEqual(job.job_context.queued_tasks_execs[1]['task_name'], 'wf.square_WLWSCTRnum_1')
    self.assertEqual(job.job_context.queued_tasks_execs[2]['task_name'], 'wf.square_WLWSCTRnum_2')
    job.notify_command_finished({'task_call_as': 'wf.square_WLWSCTRnum0', 'status': 'OK'})
    job.notify_command_finished({'task_call_as': 'wf.square_WLWSCTRnum1', 'status': 'OK'})
    job.notify_command_finished({'task_call_as': 'wf.square_WLWSCTRnum2', 'status': 'OK'})
    outputs = job.get_root_workflow_outputs()
    self.assertEqual(outputs['results'], [1, 4, 9])

  def test_scatter_on_task_output(self):
    wdl_content = '''
        workflow wf {
          call make_array
          scatter (i in make_array.res) {
            call square { input: x=i }
          }
        }
        task make_array {
          command <<< >>>
          output { Array[Int] res = [1,2,3] }
        }
        task square {
          input { Int x }
          command <<< echo $((x*x)) >>>
          output { Int result = x * x }
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob')
    task_names = {t['task_name'] for t in job.job_context.queued_tasks_execs}
    self.assertIn('wf.square_WLWSCTRi_0', task_names)
    self.assertIn('wf.square_WLWSCTRi_1', task_names)
    self.assertIn('wf.square_WLWSCTRi_2', task_names)

  def test_scatter_scatter_matrix(self):
      wdl_content = '''
      workflow wf {
        Array[Array[Int]] the_matrix = [[1,2,3],[4,5,6],[7,8,9]]
        scatter (r in the_matrix) {
          scatter (x in r) {
            call Square { input: x = x }
          }
          call Sum { input: the_arr = Square.result }
        }
        call Sum as OuterSum { input: the_arr = Sum.out }
        output { Int square_sum = OuterSum.out }
      }
      task Square { input { Int x }
                    command <<< >>>
                    output { Int result = x * x } }
      task Sum { input { Array[Int] the_arr }
                 command <<< >>>
                 output { Int out = the_arr[0] + the_arr[1] + the_arr[2] } }
      '''
      job = WDLJob('dummy', 'dummy', wdl_content)
      job.instantiate(useremail='test', s3_bucket='test', job_id='testjob')
      job.notify_command_finished({'task_call_as': 'wf.OuterSum', 'status': 'OK'})
      outputs = job.get_root_workflow_outputs()
      self.assertEqual(outputs['square_sum'], 1 + 4 + 9 + 16 + 25 + 36 + 49 + 64 + 81)

  def test_single_scatter_matrix(self):
    wdl_content = '''
        workflow wf {
          Array[Array[Int]] the_matrix = [[1,2,3],[4,5,6],[7,8,9]]
          scatter (r in the_matrix) {
            call SumOfSquares { input: r = r }
          }
          call Sum { input: the_arr = SumOfSquares.result }
          output { Int square_sum = Sum.out }
        }
        task SumOfSquares { input { Array[Int] r }
                      command <<< >>>
                      output { Int result = r[0] * r[0] + r[1] * r[1] + r[2] * r[2] } }
        task Sum { input { Array[Int] the_arr }
                   command <<< >>>
                   output { Int out = the_arr[0] + the_arr[1] + the_arr[2] } }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob')
    job.notify_command_finished({'task_call_as': 'wf.Square_WLWSCTRr_0', 'status': 'OK'})
    job.notify_command_finished({'task_call_as': 'wf.Square_WLWSCTRr_1', 'status': 'OK'})
    job.notify_command_finished({'task_call_as': 'wf.Square_WLWSCTRr_2', 'status': 'OK'})
    job.notify_command_finished({'task_call_as': 'wf.OuterSum', 'status': 'OK'})
    outputs = job.get_root_workflow_outputs()
    self.assertEqual(outputs['square_sum'], 1 + 4 + 9 + 16 + 25 + 36 + 49 + 64 + 81)


class TestWDLFileHandling(unittest.TestCase):
  def test_file_localization(self):
    wdl_content = '''
        version 1.0
        workflow wf {
          input { File input_file }
          call read_file { input: input_file=input_file }
        }
        task read_file {
          input { File input_file }
          command <<<
            cat ~{input_file}
          >>>
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'input_file': 's3://bucket/file.txt'})
    self.assertIn('wf.read_file.input_file', job.job_context.resolved_variables)
    self.assertEqual(job.job_context.resolved_variables['wf.read_file.input_file'].type, 'PENDING_FILE')
    self.assertEqual(len(job.job_context.queued_tasks_execs), 0)
    job.notify_files_available({'wf.input_file': 's3://bucket/file.txt'})
    self.assertEqual(job.job_context.resolved_variables['wf.read_file.input_file'].type, 'FINALIZED_FILE')
    self.assertEqual(job.job_context.queued_tasks_execs[0]['task_name'], 'wf.read_file')

  def test_basename(self):
    wdl_content = '''
        version 1.0
        workflow wf {
          input { File input_file }
          output { String fname = basename(input_file, ".txt") }
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'input_file': 's3://bucket/path/to/hooray.txt'})
    job.notify_files_available({'wf.input_file': 's3://bucket/path/to/hooray.txt'})
    outputs = job.get_root_workflow_outputs()
    self.assertEqual(outputs['fname'], 'hooray')

  def test_array_file_input(self):
    wdl_content = '''
        version 1.0
        workflow wf {
          input { Array[File] input_files }
          scatter (f in input_files) {
            call read_file { input: input_file = f }
          }
        }
        task read_file {
          input { File input_file }
          command <<<
            cat ~{input_file}
          >>>
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob',
                    user_inputs={'input_files': ['s3://bucket/file1.txt', 's3://bucket/file2.txt']})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 0)
    job.notify_files_available({'wf.input_files_WILLOWindex0': 's3://bucket/file1.txt',
                                'wf.input_files_WILLOWindex1': 's3://bucket/file2.txt'})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 2)
    task_names = {t['task_name'] for t in job.job_context.queued_tasks_execs}
    self.assertIn('wf.read_file_WLWSCTRf_0', task_names)
    self.assertIn('wf.read_file_WLWSCTRf_1', task_names)

  def test_file_creation_and_cloudization(self):
    wdl_content = '''
        version 1.0
        workflow wf {
          call write_file
          output { File out = write_file.out_file }
        }
        task write_file {
          command <<<
            echo "hello" > my_output.txt
          >>>
          output { File out_file = "my_output.txt" }
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob')

    # Task should be ready to run
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    task_exec_info = job.job_context.queued_tasks_execs[0]
    self.assertEqual(task_exec_info['task_name'], 'wf.write_file')
    self.assertEqual(task_exec_info['files_to_cloudize']['wf.write_file.out_file'], 'my_output.txt')
    job.job_context.queued_tasks_execs.clear()

    # Finish task
    job.notify_command_finished({'task_call_as': 'wf.write_file', 'status': 'OK'})

    # Workflow output should be pending until file is "uploaded"
    self.assertFalse(job.all_outputs_available())

    # Notify that the output file is available in the cloud
    cloud_path = 's3://test/testjob/wf.write_file/my_output.txt'
    job.notify_files_available({'wf.write_file.out_file': cloud_path})

    self.assertTrue(job.all_outputs_available())
    outputs = job.get_root_workflow_outputs()
    self.assertEqual(outputs['out'], cloud_path)


class TestTaskCompletion(unittest.TestCase):
  def test_all_outputs_available_for_zero_outputs(self):
    wdl_content = '''
        workflow wf {
          call success_task
        }
        task success_task {
          command { echo "Success" }
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob')
    self.assertIn('wf.success_task', job.root_called_tasks_for_test())
    job.notify_command_finished({'task_call_as': 'wf.success_task', 'status': 'OK'})
    self.assertFalse(job.any_pending())
    self.assertTrue(job.all_outputs_available())

  def test_task_success(self):
    wdl_content = '''
        workflow wf {
          call success_task
          output { String res = success_task.result }
        }
        task success_task {
          command { echo "Success" }
          output { String result = "Success" }
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob')
    self.assertIn('wf.success_task', job.root_called_tasks_for_test())
    job.notify_command_finished({'task_call_as': 'wf.success_task', 'status': 'OK'})
    self.assertTrue(job.all_outputs_available())
    outputs = job.get_root_workflow_outputs()
    self.assertEqual(outputs['res'], 'Success')

  def test_value_propagation(self):
    wdl_content = '''
        workflow wf {
          input { String hello }
          call success_task as differentName { input: hello = hello }
          output { String res = differentName.goodbye }
        }
        task success_task {
          input { String hello }
          command { echo "~{hello}" }
          output { String goodbye = hello }
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'hello': 'yo'})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    self.assertEqual(job.job_context.queued_tasks_execs[0]['task_name'], 'wf.differentName')
    self.assertIn('wf.differentName', job.root_called_tasks_for_test())
    job.notify_command_finished({'task_call_as': 'wf.differentName', 'status': 'OK'})
    self.assertTrue(job.all_outputs_available())
    outputs = job.get_root_workflow_outputs()
    self.assertEqual(outputs['res'], 'yo')

  def test_add_with_null_operand(self):
    tf = ResolutionTF('test', JobContext('testjob', 'test', 'test', [], [], {}, ResolvedVariables()))
    # Left operand is null
    left_null = WILLOWNULL
    right_int = Token('FINALIZED_INT', 5)
    with self.assertRaisesRegex(ValueError, 'null is not allowed in addition expression'):
      tf.add([left_null, right_int])
    # Right operand is null
    left_int = Token('FINALIZED_INT', 5)
    right_null = WILLOWNULL
    with self.assertRaisesRegex(ValueError, 'null is not allowed in addition expression'):
      tf.add([left_int, right_null])

  def test_land_with_null_operand(self):
    tf = ResolutionTF('test', JobContext('testjob', 'test', 'test', [], [], {}, ResolvedVariables()))
    # Left operand is null
    left_null = WILLOWNULL
    right_bool = Token('FINALIZED_BOOLEAN', True)
    with self.assertRaisesRegex(ValueError, 'null is not allowed in logical-AND expression'):
      tf.land([left_null, right_bool])
    # Right operand is null
    left_bool = Token('FINALIZED_BOOLEAN', True)
    right_null = WILLOWNULL
    with self.assertRaisesRegex(ValueError, 'null is not allowed in logical-AND expression'):
      tf.land([left_bool, right_null])

  def test_eqeq_with_null_operand(self):
    tf = ResolutionTF('test', JobContext('testjob', 'test', 'test', [], [], {}, ResolvedVariables()))
    # Left operand is null
    left_null = WILLOWNULL
    right_int = Token('FINALIZED_INT', 5)
    with self.assertRaisesRegex(ValueError, 'null is not allowed in eqeq expression'):
      tf.eqeq([left_null, right_int])
    # Right operand is null
    left_int = Token('FINALIZED_INT', 5)
    right_null = WILLOWNULL
    with self.assertRaisesRegex(ValueError, 'null is not allowed in eqeq expression'):
      tf.eqeq([left_int, right_null])

  def test_at_with_null_operand(self):
    tf = ResolutionTF('test', JobContext('testjob', 'test', 'test', [], [], {}, ResolvedVariables()))
    # Array operand is null
    null_arr = WILLOWNULL
    index_int = Token('FINALIZED_INT', 1)
    with self.assertRaisesRegex(ValueError, 'null is not allowed in array access'):
      tf.at([null_arr, index_int])
    # Index operand is null
    arr_token = Token('FINALIZED_ARRAY', [10, 20, 30])
    null_index = WILLOWNULL
    with self.assertRaisesRegex(ValueError, 'null is not allowed in array access'):
      tf.at([arr_token, null_index])

  # TODO this would need to be a test over the whole the_widdler.py setup
  #def test_task_failure(self):

class TestWDLChainedTasks(unittest.TestCase):
  # demonstrates task chaining, in the case where all variables can propagate all the way through
  # beofre the task commands are even run.
  # TODO add a file-contents-based one, so each step has to wait for file available
  def test_simple_chain(self):
    wdl_content = '''
        workflow wf {
          input { String name }
          call noop { input: name = name }
          call greet { input: name = noop.the_out }
          call exclaim { input: message = greet.greeting }
          output { String final_message = exclaim.exclamation }
        }
        task noop {
          input { String name }
          command <<< echo "Hello constant string" >>>
          output { String the_out = "noop-" + name }
        }
        task greet {
          input { String name }
          command <<<
            echo "Hello, ~{name}!"
          >>>
          output { String greeting = "Hello, " + name + "!" }
        }
        task exclaim {
          input { String message }
          command <<< echo "~{message}!!" >>>
          output { String exclamation = message + "!!" }
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob', user_inputs={'name': 'World'})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 3)

    # Launch the tasks, and they all finish
    job.job_context.queued_tasks_execs.clear()
    job.notify_command_finished({'task_call_as': 'wf.noop', 'status': 'OK'})
    job.notify_command_finished({'task_call_as': 'wf.greet', 'status': 'OK'})
    job.notify_command_finished({'task_call_as': 'wf.exclaim', 'status': 'OK'})

    self.assertTrue(job.all_outputs_available())
    self.assertFalse(job.any_pending())
    outputs = job.get_root_workflow_outputs()
    self.assertEqual(outputs['final_message'], 'Hello, noop-World!!!')

# TODO not yet supported
# class TestWDLNestedConstructs(unittest.TestCase):
#     def test_nested_scatter(self):
#         wdl_content = '''
#         workflow wf {
#           input { Array[Array[Int]] matrix }
#           scatter (row in matrix) {
#             scatter (val in row) {
#               call square { input: x=val }
#             }
#           }
#         }
#         task square {
#           input { Int x }
#           command <<<
#             echo $((x * x))
#           >>>
#           output { Int result = x * x }
#         }
#         '''
#         job = WDLJob('dummy', 'dummy', wdl_content)
#         job.instantiate(useremail='test', s3_bucket='test', job_id='testjob')
#         job.accept_user_inputs({'matrix': [[1, 2], [3]]})
#
#         self.assertEqual(len(job.root_called_tasks_for_test()), 3)
#         # Suffixes should be outer-scatter-var then inner-scatter-var
#         expected_tasks = [
#             'wf.square_WLWSCTRrow_0_WLWSCTRval0',
#             'wf.square_WLWSCTRrow_0_WLWSCTRval1',
#             'wf.square_WLWSCTRrow_1_WLWSCTRval0'
#         ]
#         for task_name in expected_tasks:
#             self.assertIn(task_name, job.root_called_tasks_for_test())
#
#         self.assertEqual(len(job.job_context.queued_tasks_execs), 3)
#         queued_task_names = {t['task_name'] for t in job.job_context.queued_tasks_execs}
#         for task_name in expected_tasks:
#             self.assertIn(task_name, queued_task_names)

  def test_overridden_defaults_shouldnt_propagate(self):
    wdl_content = '''
      workflow wf {
        input {
          Int? c
          Int  d = 4
          Int  e = 5
        }
        call t {
          input:
          c = c, d = d, to_intermediate = e
        }
        output {
          Int out_b = t.out_b
          Int out_c = t.out_c
          Int out_d = t.out_d
          Int out_e = t.out_e
        }
      }

      task t {
        input {
          Int? a
          Int  b = 2
          Int? c
          Int  d = 4
          Int  to_intermediate = 5
        }
        Int intermediate = to_intermediate
        command <<< echo "a is: ~{a}" >>>
        output {
          Int out_b = b * 10
          Int out_c = c * 10
          Int out_d = d * 10
          Int out_e = intermediate * 10
        }
        runtime {
          willow_image_name: "default"
          docker: "none"
          memory: "120 MiB"
        }
      }
      '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob',
                    user_inputs={'c': '300', 'd': '400', 'e': '500'})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    job.notify_command_finished({'task_call_as': 'wf.t', 'status': 'OK'})
    self.assertTrue(job.all_outputs_available())
    outputs = job.get_root_workflow_outputs()
    self.assertEqual(outputs['out_b'], 20)
    self.assertEqual(outputs['out_c'], 3000)
    self.assertEqual(outputs['out_d'], 4000)
    self.assertEqual(outputs['out_e'], 5000)

  def test_can_pass_file_to_string_input(self):
    wdl_content = '''
workflow wf {
  input { File f }
  call t { input: s = f }
  output { String the_output = t.out }
}
task t {
  input { String s }
  command <<< basename ~{s} .extension >>>
  output { String out = s + "hi hiii" }
}'''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob',
                    user_inputs={'f': 's3://bucket/path/to/file_name.extension'})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 0)
    job.notify_files_available({'wf.f': 's3://bucket/path/to/file_name.extension'})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)

    task_info = job.job_context.queued_tasks_execs[0]
    command = zlib.decompress(base64.b64decode(task_info['bash_cmds'])).decode('utf-8')
    self.assertTrue('basename s3://bucket/path/to/file_name.extension .extension' in command)

    job.notify_command_finished({'task_call_as': 'wf.t', 'status': 'OK'})
    self.assertTrue(job.all_outputs_available())
    outputs = job.get_root_workflow_outputs()
    self.assertEqual(outputs['the_output'], 's3://bucket/path/to/file_name.extensionhi hiii')


class TestWDLErrorHandling(unittest.TestCase):
  def test_missing_mandatory_input_in_call(self):
    with self.assertRaisesRegex(lark.exceptions.VisitError, 'non-optional input in_name receives no value'):
      wdl_content = '''
            workflow wf {
                call t
            }
            task t {
                input { String in_name }
                command <<< echo ~{in_name} >>>
            }
            '''
      job = WDLJob('dummy', 'dummy', wdl_content)
      job.instantiate(useremail='test', s3_bucket='test', job_id='testjob')


FAKE_END_TO_END_TEST_CONTENT = '''version 1.0

workflow FakeEndToEndTest {
  input {
    File the_input_file
    Boolean set_me_false
    Boolean set_me_true
    Int set_me_3
  }
  call capitalize {
    input:
    cap_in = the_input_file
  }
  call reverse {
    input:
    rev_in = capitalize.cap_out
  }
  if (set_me_false) {
    call capitalize as capitalize_expect_NOT_run0 {
      input:
      cap_in = the_input_file
    }
  }
  if (false) {
    if (true) {
      call capitalize as capitalize_expect_NOT_run1 {
        input:
        cap_in = the_input_file
      }
    }
  }
  if (set_me_true) {
    call capitalize as capitalize_expect_to_run0 {
      input:
      cap_in = the_input_file
    }
  }
  if (set_me_3 == 3) {
    call capitalize as capitalize_expect_to_run1 {
      input:
      cap_in = the_input_file
    }
  }
  if (set_me_3 != 3) {
    call capitalize as capitalize_expect_to_NOT_run2 {
      input:
      cap_in = the_input_file
    }
  }
  if (set_me_3 > 3) {
    call capitalize as capitalize_expect_to_NOT_run3 {
      input:
      cap_in = the_input_file
    }
  }
  Array[Int] const_array_input = [3, 5, 29]
  scatter (x in const_array_input) {
    call scatterIncrement as scttr { input: x = x }
  }
  call gatherSum as gathersum { input: nums = scttr.y }
  output {
    File the_output = reverse.rev_out
    Int the_incremented_sum = gathersum.the_sum
  }
}

task capitalize {
  input {
    File cap_in
  }
  command <<<
    tr '[:lower:]' '[:upper:]' <~{cap_in} >in_vm_capitalizedDDDD
    echo "here is some stdout for you"
  >>>
  output {
    File cap_out = "in_vm_capitalizedDDDD"
  }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
  }
}

task reverse {
  input {
    File rev_in
  }
  command <<<
    tac ~{rev_in} >in_vm_this_is_my_test_output_filename
  >>>
  output {
    File rev_out = "in_vm_this_is_my_test_output_filename"
  }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
  }
}

task scatterIncrement {
  input { Int x }
  command <<<
    echo "this x is ~{x}"
  >>>
  output { Int y = x + 1 }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
  }
}

task gatherSum {
  input { Array[Int] nums }
  command <<<
    echo hi from gather
  >>>
  output { Int the_sum = nums[0] + nums[1] + nums[2] }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
  }
}
'''
class TestSimplifiedE2ETests(unittest.TestCase):
  def test_fake_e2e_test(self):
    job = WDLJob('dummy', 'dummy', FAKE_END_TO_END_TEST_CONTENT)
    job.instantiate(useremail='test',
                    s3_bucket='test',
                    job_id='testjob',
                    user_inputs={'the_input_file': 's3://willowtestuploads/whoa',
                                 'set_me_false': False,
                                 'set_me_true': True,
                                 'set_me_3': 3})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 4)
    job.notify_files_available({'FakeEndToEndTest.the_input_file': 's3://willowtestuploads/whoa'})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 7)
    job.notify_files_available({'FakeEndToEndTest.capitalize_expect_to_run.cap_out': 's3://test/testjob/FakeEndToEndTest.capitalize_expect_to_run/in_vm_capitalizedDDDD',
                                'FakeEndToEndTest.capitalize.cap_out': 's3://test/testjob/FakeEndToEndTest.capitalize/in_vm_capitalizedDDDD'})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 8)
    job.notify_files_available({'FakeEndToEndTest.reverse.rev_out': 's3://test/testjob/FakeEndToEndTest.reverse/in_vm_this_is_my_test_output_filename'})
    self.assertTrue(job.all_outputs_available())
    self.assertIn('the_output', job.get_root_workflow_outputs())
    self.assertEqual(job.get_root_workflow_outputs()['the_incremented_sum'], 40)

class TestWDLStructs(unittest.TestCase):
  def test_struct_simple(self):
    wdl_content = '''
        workflow wf {
          input { String who MyStruct the_struct }
          call Greet {
            input: the_sum = the_struct.a + the_struct.b
          }
        }
        struct MyStruct {
          Int a
          Int b
        }
        task Greet {
          input {
            Int the_sum
          }
          command <<<
            echo "Hello, ~{the_sum}!"
          >>>
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate('test', 'test', 'testjob', user_inputs={'who': 'World', 'the_struct': {'a': 1, 'b': 2}})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    task_info = job.job_context.queued_tasks_execs[0]
    command = zlib.decompress(base64.b64decode(task_info['bash_cmds'])).decode('utf-8')
    self.assertTrue('echo "Hello, 3!"' in command)

  def test_struct_with_file(self):
    wdl_content = '''
        workflow wf {
          input { MyStruct the_struct }
          call Greet {
            input: the_file = the_struct.f
          }
        }
        struct MyStruct {
          File f
        }
        task Greet {
          input {
            File the_file
          }
          command <<<
            cat ~{the_file}
          >>>
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate('test', 'test', 'testjob', user_inputs={'the_struct': {'f': 's3://willowtestuploads/whoa'}})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 0)
    job.notify_files_available({'wf.the_struct.f': 's3://willowtestuploads/whoa'})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    task_info = job.job_context.queued_tasks_execs[0]
    command = zlib.decompress(base64.b64decode(task_info['bash_cmds'])).decode('utf-8')
    self.assertTrue('cat whoa' in command)

  def test_struct_with_filearray(self):
    wdl_content = '''
        workflow wf {
          input { MyStruct the_struct }
          call Greet {
            input: the_files = the_struct.f
          }
        }
        struct MyStruct {
          Array[File] f
        }
        task Greet {
          input {
            Array[File] the_files
          }
          command <<<
            cat ~{the_files[0]} ~{the_files[1]}
          >>>
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate('test', 'test', 'testjob', user_inputs={'the_struct': {'f': ['s3://willowtestuploads/whoa', 's3://willowtestuploads/dude']}})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 0)
    job.notify_files_available({'wf.the_struct.f_WILLOWindex0': 's3://willowtestuploads/whoa'})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 0)
    job.notify_files_available({'wf.the_struct.f_WILLOWindex1': 's3://willowtestuploads/dude'})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    task_info = job.job_context.queued_tasks_execs[0]
    command = zlib.decompress(base64.b64decode(task_info['bash_cmds'])).decode('utf-8')
    self.assertTrue('cat whoa dude' in command)

  def test_struct_in_struct(self):
    wdl_content = '''
        workflow wf {
          input { String who MyStruct the_struct }
          call Greet {
            input: the_str = the_struct.bar.s
          }
        }
        struct MyStruct {
          Int a
          Int b
          Bar bar
        }
        struct Bar {
          String s
        }
        task Greet {
          input {
            String the_str
          }
          command <<<
            echo "Hello, ~{the_str}!"
          >>>
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate('test', 'test', 'testjob', user_inputs={'who': 'World', 'the_struct': {'a': 1, 'b': 2, 'bar': {'s': 'Widdler'}}})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    task_info = job.job_context.queued_tasks_execs[0]
    command = zlib.decompress(base64.b64decode(task_info['bash_cmds'])).decode('utf-8')
    self.assertTrue('echo "Hello, Widdler!"' in command)


CONDITIONAL_ON_OUTPUT_WDL = '''
workflow wf {
  input { Boolean should_do_second_task }
  call check { input: should_do_second_task=should_do_second_task }
  if (check.proceed) {
    call echo { input: msg="hello" }
  }
}
task check {
  input { Boolean should_do_second_task }
  command <<< >>>
  output { Boolean proceed = should_do_second_task }
}
task echo {
  input { String msg }
  command <<< echo ~{msg} >>>
}
'''

class TestCallCaching(unittest.TestCase):
  # NOTE: out_thing was originally just "x*2", and the actual 123*2 value was making it through to
  #       the workflow output, I think because 1) if variables can propagate even without the task
  #       running, they'll do so, and 2) even though the cache result might overwrite the task
  #       output, that wouldn't change the already-propagated-to workflow output.
  #
  #       So anyways, I'm making it hang with the read_int() so that it actually tests caching.
  def test_simple_call_caching_hit(self):
    job = WDLJob('dummy', 'dummy',
                 '''
                 version 1.0
                 workflow wf {
                   input { Int foo
                           File dummy }
                   output { Int bar = t.out_thing }
                   call t { input: x = foo, dummy = dummy }
                 }
                 task t {
                   input { Int x
                           File dummy }
                   command <<< cat ~{dummy} >lol >>>
                   output { Int out_thing = x * read_int("lol") }
                 }
                 ''')
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob',
                    user_inputs={'foo': 123, 'dummy': 's3://bucket/whatever'})
    job.notify_files_available({'wf.dummy': 's3://bucket/whatever'})
    job.notify_task_cache_hit('wf.t', {'out_thing': Token('FINALIZED_INT', 111)})
    outputs = job.get_root_workflow_outputs()
    self.assertEqual(outputs['bar'], 111)

TEST_OPTIONAL_WDL = '''workflow TestOptional {
  input {
    File? maybe_file
    Array[File]? maybe_file_array
    String? maybe_string
    Int? maybe_int
    Int default_int = 123
  }
  call ALLCAPS {
    input:
    cap_in = maybe_file,
    maybe_file_array = maybe_file_array,
    maybe_string = maybe_string,
    maybe_int = maybe_int,
    definitely_int = default_int
  }
  output {
    File the_output = ALLCAPS.cap_out
  }
}

task ALLCAPS {
  input {
    File? cap_in
    Array[File]? maybe_file_array
    String? maybe_string
    Int? maybe_int
    Int definitely_int
  }
  command <<<
    if [ -n "~{cap_in}" ]; then
      tr '[:lower:]' '[:upper:]' <~{cap_in} >wow_cool_i_am_CAPITALIZED
    else
      echo "NO INPUT FILE PROVIDED" >wow_cool_i_am_CAPITALIZED
    fi
    echo "maybe_string is ~{maybe_string}" >>wow_cool_i_am_CAPITALIZED
    echo "maybe_int is ~{maybe_int}" >>wow_cool_i_am_CAPITALIZED
    echo "definitely_int is ~{definitely_int}" >>wow_cool_i_am_CAPITALIZED
    echo "" >>wow_cool_i_am_CAPITALIZED
    cat ~{maybe_file_array} >>wow_cool_i_am_CAPITALIZED
    echo "here is some stdout for you"
  >>>
  output {
    File cap_out = "wow_cool_i_am_CAPITALIZED"
  }
}'''

class TestOptionalAndDefaults(unittest.TestCase):
  def test_optional_input_with_default(self):
    wdl_content = '''
        workflow wf {
          call t
          output { String res = t.out }
        }
        task t {
          input { String name = "default" }
          command { echo "~{name}" }
          output { String out = name }
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob')
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    job.notify_command_finished({'task_call_as': 'wf.t', 'status': 'OK'})
    self.assertTrue(job.all_outputs_available())
    outputs = job.get_root_workflow_outputs()
    self.assertEqual(outputs['res'], 'default')

  def test_optional_input_overridden(self):
    wdl_content = '''
        workflow wf {
          call t { input: name = "override" }
          output { String res = t.out }
        }
        task t {
          input { String name = "default" }
          command { echo "~{name}" }
          output { String out = name }
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob')
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    job.notify_command_finished({'task_call_as': 'wf.t', 'status': 'OK'})
    self.assertTrue(job.all_outputs_available())
    outputs = job.get_root_workflow_outputs()
    self.assertEqual(outputs['res'], 'override')

  def test_fake_test_optional(self):
    job = WDLJob('dummy', 'dummy', TEST_OPTIONAL_WDL)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob',
                    user_inputs={'maybe_int': 11, 'default_int': 22, 'maybe_file': 's3://bucket/whatever',
                                 'maybe_file_array': ['s3://bucket/one', 's3://bucket/two']})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 0)
    job.notify_files_available({'TestOptional.maybe_file': 's3://bucket/whatever',
                                'TestOptional.maybe_file_array_WILLOWindex0': 's3://bucket/one',
                                'TestOptional.maybe_file_array_WILLOWindex1': 's3://bucket/two'})
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    task_info = job.job_context.queued_tasks_execs[0]
    command = zlib.decompress(base64.b64decode(task_info['bash_cmds'])).decode('utf-8')
    self.assertTrue('maybe_int is 11' in command)
    self.assertTrue('definitely_int is 22' in command)
    self.assertTrue('cat one two' in command)
    job.notify_command_finished({'task_call_as': 'TestOptional.ALLCAPS', 'status': 'OK'})
    job.notify_files_available({'TestOptional.ALLCAPS.cap_out': 's3://bucket/lol'})
    self.assertTrue(job.all_outputs_available())

  def gross_nullable_bash_substition(self):
    wdl_content = '''
        workflow wf {
          call t { input: name = "override" }
          output { String res = t.out }
        }
        task t {
          input { String name = "default"
                  File? gross_optional }
          command <<< echo just "~{"--gross "+gross_optional}" this >>>
          output { String out = name }
        }
        '''
    job = WDLJob('dummy', 'dummy', wdl_content)
    job.instantiate(useremail='test', s3_bucket='test', job_id='testjob')
    self.assertEqual(len(job.job_context.queued_tasks_execs), 1)
    task_info = job.job_context.queued_tasks_execs[0]
    command = zlib.decompress(base64.b64decode(task_info['bash_cmds'])).decode('utf-8')
    self.assertTrue('echo just "" this' in command)

if __name__ == '__main__':
  unittest.main()
