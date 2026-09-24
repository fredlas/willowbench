version 1.0

workflow TestCallCacheHalfway {
  input {
    String s
    Boolean intentionally_fail
  }
  call WriteFile { input: s = s }
  call MaybeFail { input: f = intentionally_fail, dummy = WriteFile.dummy }
  output {
    File the_output = WriteFile.outfile
    Boolean did_we_want_to_fail = MaybeFail.fail
  }
}

task WriteFile {
  input {
    String s
  }
  command <<<
    echo "hello world, your string was: ~{s}" >the_outfile
    echo "here is some stdout for you!"
    echo "123" >dummy
  >>>
  output {
    File outfile = "the_outfile"
    Int dummy = read_int("dummy")
    File? ok_to_not_have = "hkguytguyyyyyyyy"
  }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
  }
}

task MaybeFail {
  input {
    Boolean f
    Int dummy
  }
  String maybe_fail = if f then "exit 1" else " "
  command <<<
    ~{maybe_fail}
    echo "didnt fail! dummy is ~{dummy}"
  >>>
  output { Boolean fail = f}
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
  }
}
