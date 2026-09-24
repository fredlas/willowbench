version 1.0

import "RealImportee.wdl" as Impy

workflow RealEndToEndTest {
  input {
    File the_input_file
    Boolean set_me_false
    Boolean set_me_true
    Int set_me_3
    HeresMyStruct foo
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

  call CatFileArray { input: arr = foo.is.file_arr }
  call Impy.TheImportee { input: x = "hello", should_run = false }
  call Impy.TheImportee as ImpAs { input: x = "goodbye", should_run = true }
  call Impy.ImpTask { input: tx = "tasky" }

  output {
    File the_output = reverse.rev_out
    File catted = CatFileArray.catted
    Int the_incremented_sum = gathersum.the_sum
    Int ungathered = select_first(scttrONE.y)
    String import_output1 = Impy.TheImportee.imp_out
    String import_output2 = ImpAs.imp_out
    String import_output3 = Impy.ImpTask.task_out
    Int imported_gather_out = ImpAs.gathered
    Int imp_conditional_out_true = ImpAs.conditional_out
    Int imp_conditional_out_false = Impy.TheImportee.conditional_out
    Int struct_sum_out = foo.a + foo.b + foo.is.c
  }
}

task CatFileArray {
  input { Array[File] arr }
  command <<< cat ~{arr} >catted >>>
  output { File catted = "catted" }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
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

task gather2 {
  input { Array[Int] nums }
  command <<<
    echo hi from gather2
  >>>
  output { Int the_sum = nums[0] + nums[1] }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
  }
}
