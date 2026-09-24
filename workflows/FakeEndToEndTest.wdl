version 1.0

import "Importee.wdl" as Impy

workflow FakeEndToEndTest {
  input {
    File the_input_file
    Boolean set_me_false
    Boolean set_me_true
    Int set_me_3
    HeresMyStruct foo
    Array[Int] two_input_ints
    Array[File] two_input_files
  }
  call capitalize {
    input:
    cap_in = the_input_file,
    fname_suffix = "normal"
  }
  call reverse {
    input:
    rev_in = capitalize.cap_out
  }
  if (set_me_false) {
    call capitalize as capitalize_expect_NOT_run0 {
      input:
      cap_in = the_input_file,
      fname_suffix = "nope"
    }
  }
  if (false) {
    if (true) {
      call capitalize as capitalize_expect_NOT_run1 {
        input:
        cap_in = the_input_file,
        fname_suffix = "alsonope"
      }
    }
  }
  if (set_me_true) {
    call capitalize as capitalize_expect_to_run0 {
      input:
      cap_in = the_input_file,
      fname_suffix = "yep0"
    }
  }
  if (set_me_3 == 3) {
    call capitalize as capitalize_expect_to_run1 {
      input:
      cap_in = the_input_file,
      fname_suffix = "yep1"
    }
  }
  if (set_me_3 != 3) {
    call capitalize as capitalize_expect_to_NOT_run2 {
      input:
      cap_in = the_input_file,
      fname_suffix = "nope2"
    }
  }
  if (set_me_3 > 3) {
    call capitalize as capitalize_expect_to_NOT_run3 {
      input:
      cap_in = the_input_file,
      fname_suffix = "nope3"
    }
  }
  Array[Int] const_array_input = [3, 5, 29]
  scatter (x in const_array_input) {
    if (true) {
      call scatterIncrement as scttr { input: x = x }
    }
  }
  call gatherSum as gathersum { input: nums = scttr.y }

  scatter (x in const_array_input) {
    if (x > 3 && x < 20) {
      call scatterIncrement as scttrONE { input: x = x }
    }
  }
  scatter (x in const_array_input) {
    if (false) {
      call scatterIncrement as scttrNOT_AT_ALL { input: x = x }
    }
  }

  call Impy.TheImportee { input: x = "hello", should_run = false }
  call Impy.TheImportee as ImpAs { input: x = "goodbye", should_run = true }
  call Impy.ImpTask { input: tx = "tasky" }
  call justforthefile
  call catNumbersArray { input: nums = two_input_ints, files = two_input_files }

  Array[String] sigh1 = justforthefile.tsv_to_matrix[0]
  Array[String] sigh2 = justforthefile.tsv_to_matrix[1]

  output {
    File the_output = reverse.rev_out
    Int the_incremented_sum = gathersum.the_sum
    Int the_read_int_stdout = gathersum.the_stdout
    Float the_read_float_file = justforthefile.the_read_float_file
    File globbed_0 = justforthefile.globbed[0]
    File globbed_1 = justforthefile.globbed[1]
    Int ungathered = select_first(scttrONE.y)
    String import_output1 = Impy.TheImportee.imp_out
    String import_output2 = ImpAs.imp_out
    String import_output3 = Impy.ImpTask.task_out
    Int imported_gather_out = ImpAs.gathered
    Int imp_conditional_out_true = ImpAs.conditional_out
    Int imp_conditional_out_false = Impy.TheImportee.conditional_out
    Int struct_sum_out = foo.a + foo.b + foo.is.c
    String oolong = catNumbersArray.cat_strings[0]
    String lily = catNumbersArray.cat_strings[1]
    File oolong_file = catNumbersArray.cats[0]
    File lily_file = catNumbersArray.cats[1]
    Boolean read_tsv_success = (sigh1[0] == "hello" && sigh1[1] == "world" &&
                                sigh2[0] == "HELLO" && sigh2[1] == "WORLD")
  }
}

task capitalize {
  input {
    File cap_in
    String fname_suffix
  }
  command <<<
    tr '[:lower:]' '[:upper:]' <~{cap_in} >in_vm_capitalized_~{fname_suffix}
    echo "here is some stdout for you"
  >>>
  output {
    File cap_out = "in_vm_capitalized_" + fname_suffix
  }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
    use_fake_runner_in_dev: "true"
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
    use_fake_runner_in_dev: "true"
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
    use_fake_runner_in_dev: "true"
  }
}

task gatherSum {
  input { Array[Int] nums }
  command <<<
    echo 123987
  >>>
  output { Int the_sum = nums[0] + nums[1] + nums[2]
           Int the_stdout = read_int(stdout()) }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
    use_fake_runner_in_dev: "true"
  }
}

task justforthefile {
  command <<<
    echo "3.14159" >some_file.txt
    echo 7 >int.1.txt
    echo 550 >int.2.txt
    echo -e "hello\tworld\nHELLO\tWORLD" >the_tsv.tsv
  >>>
  output { Float the_read_float_file = read_float("some_file.txt")
           Array[File] globbed = glob("int.*.txt")
           Array[Array[String]] tsv_to_matrix = read_tsv("the_tsv.tsv") }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
    use_fake_runner_in_dev: "true"
  }
}

task catNumbersArray {
  input { Array[Int] nums
          Array[File] files }
  command <<<
    cat ~{files[0]} >oolong
    echo ~{nums[0]} >>oolong
    cat ~{files[1]} >lily
    echo ~{nums[1]} >>lily
  >>>
  output { Array[File] cats = ["oolong", "lily"]
           Array[String] cat_strings = [read_string("oolong"), read_string("lily")] }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
    use_fake_runner_in_dev: "true"
  }
}
