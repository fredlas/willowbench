workflow ReadIntStdout {
  input { Array[Int] in }
  call Sum { input: in = in }
  output { Int the_sum = Sum.out
           Array[File] out_file_twice = [Sum.file, Sum.file] }
}

task Sum {
  input { Array[Int] in }
  command <<<
    echo "in array: ~{in}" >array_echo
    echo "777"
  >>>
  output { Int out = read_int(stdout())
           File file = "array_echo" }
}
