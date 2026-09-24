workflow TestReadFileStdoutDocker {
  call TheTask
  output {
    Float f = TheTask.f
    String the_stdout = TheTask.the_stdout
    Int the_int_stdout = TheTask.the_int_stdout
  }
}

task TheTask {
  command <<<
    echo "3.14159" >some_file.txt
    echo 777
  >>>
  output { Float f = read_float("some_file.txt")
           String the_stdout = stdout()
           Int the_int_stdout = read_int(stdout()) }
}
