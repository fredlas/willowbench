version 1.0

workflow simpletest {
  input {
    File the_input_file
  }
  call capitalize {
    input:
    cap_in = the_input_file
  }
  call spaces_to_newlines {
    input:
    spacenew_in = capitalize.cap_out
  }
  call reverse {
    input:
    rev_in = spaces_to_newlines.spacenew_out
  }
  output {
    File the_output = reverse.rev_out
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
    docker: "alpine"
    memory: "120 MiB"
  }
}

task spaces_to_newlines {
  input {
    File spacenew_in
  }
  command <<<
    tr ' ' '\n' <~{spacenew_in} >in_vm_brokenuplololol
  >>>
  output {
    File spacenew_out = "in_vm_brokenuplololol"
  }
}

task reverse {
  input {
    File rev_in
  }
  command <<<
    tac ~{rev_in} >in_vm_this_is_my_test_output_filename.txt
  >>>
  output {
    File rev_out = "in_vm_this_is_my_test_output_filename.txt"
  }
}
