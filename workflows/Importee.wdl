version 1.0
workflow TheImportee {
  input { String x
          Boolean should_run }
  call ImpTask { input: tx = x }
  scatter (x in [1, 2, 3]) {
    call BasicScatterTask as IMPscttr { input: si = x }
  }
  call ImportedGatherSum { input: nums = IMPscttr.siy }
  if (should_run) {
    call ImpConditionalTask
  }
  output { String imp_out = ImpTask.task_out
           Int gathered = ImportedGatherSum.the_sum
           Int conditional_out = if should_run then ImpConditionalTask.imp_cond_task_out else 999 }
}

struct HeresMyStruct {
  Int a
  Int b
  InnerStruct is
}

struct InnerStruct {
  Int c
}

task ImpTask {
  input { String tx }
  command <<<
    echo ~{tx}
  >>>
  output { String task_out = tx + tx }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
    use_fake_runner_in_dev: "true"
  }
}

task BasicScatterTask {
  input { Int si }
  command <<<
    echo "this si is ~{si}"
  >>>
  output { Int siy = si }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
    use_fake_runner_in_dev: "true"
  }
}

task ImportedGatherSum {
  input { Array[Int] nums }
  command <<<
    echo hi from gather
  >>>
  output { Int the_sum = nums[0] + nums[1] + nums[2] }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
    use_fake_runner_in_dev: "true"
  }
}

task ImpConditionalTask {
  command <<<
    echo "yup an ImpConditionalTask ran"
  >>>
  output { Int imp_cond_task_out = 333 }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
    use_fake_runner_in_dev: "true"
  }
}
