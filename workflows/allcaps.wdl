version 1.0

workflow allcaps {
  input {
    File the_input_file
  }
  call ALLCAPS {
    input:
    cap_in = the_input_file
  }
  output {
    File the_output = ALLCAPS.cap_out
  }
}

task ALLCAPS {
  input {
    File cap_in
  }
  command <<<
    tr '[:lower:]' '[:upper:]' <~{cap_in} >wow_cool_i_am_CAPITALIZED.txt
    echo "here is some stdout for you"
  >>>
  output {
    File cap_out = "wow_cool_i_am_CAPITALIZED.txt"
  }
}
