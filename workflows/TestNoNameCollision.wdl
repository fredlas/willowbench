version 1.0

workflow TestNoNameCollision {
  input { File the_input_file }
  call Thing { input: file_in = the_input_file }
  output { File the_output = Thing.the_out }
}

task Thing {
  input {
    File file_in
  }
  command <<<
    cat ~{file_in} format_outpost.py outpost.py outpost_stderr outpost_template.py willow_task_config.json run_bundled_willow_task_commands.sh /willow_system/outpost_template.py /willow_system/run_bundled_willow_task_commands.sh >all_catted.txt
  >>>
  output {
    File the_out = "all_catted.txt"
  }
}
