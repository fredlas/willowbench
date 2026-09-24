version 1.0

workflow SeventyFiveSeconds {
  input {
    String the_string
  }
  call Delayed {
    input:
    the_str = the_string
  }
  output {
    String the_output = Delayed.str_out
  }
}

task Delayed {
  input {
    String the_str
  }
  command <<<
    echo "now sleeping for 75 seconds start time: `date`"
    sleep 75
    echo "done sleeping, end time: `date`"
    echo "your input string was: ~{the_str}"
  >>>
  output {
    String str_out = the_str
  }
}
