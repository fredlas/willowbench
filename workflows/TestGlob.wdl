workflow TestGlob {
  call TheTask
  output {
    File f1 = TheTask.globbed[0]
    File f2 = TheTask.globbed[1]
  }
}

task TheTask {
  command <<<
    echo 111 >int.1.txt
    echo 222000 >int.2.txt
  >>>
  output { Array[File] globbed = glob("int.*.txt") }
}
