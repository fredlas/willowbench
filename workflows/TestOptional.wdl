version 1.0

struct HeresMyStruct {
  InnerStruct is
}

struct InnerStruct {
  Array[File] file_arr
}

workflow TestOptional {
  input {
    File? maybe_file
    Array[File]? maybe_file_array
    String? maybe_string
    Int? maybe_int
    Int default_int = 123
    HeresMyStruct? maybe_struct
    Array[File]? leave_empty_file_array
  }
  call ALLCAPS {
    input:
    cap_in = maybe_file,
    maybe_file_array = maybe_file_array,
    maybe_FA_from_struct = maybe_struct.is.file_arr,
    maybe_string = maybe_string,
    maybe_int = maybe_int,
    definitely_int = default_int,
    leave_empty_file_array = leave_empty_file_array
  }
  output {
    File the_output = ALLCAPS.cap_out
    File? nope = ALLCAPS.nope
  }
}

task ALLCAPS {
  input {
    File? cap_in
    Array[File]? maybe_file_array
    Array[File]? maybe_FA_from_struct
    String? maybe_string
    Int? maybe_int
    Int definitely_int
    Array[File]? leave_empty_file_array
  }
  command <<<
    if [ -n "~{cap_in}" ]; then
      #tr '[:lower:]' '[:upper:]' <~{cap_in} >wow_cool_i_am_CAPITALIZED
      echo "weird, cap_in is ~{cap_in}." >wow_cool_i_am_CAPITALIZED
    else
      echo "NO INPUT FILE PROVIDED" >wow_cool_i_am_CAPITALIZED
    fi

    cat /willow_system/run_bundled_willow_task_commands.sh >>wow_cool_i_am_CAPITALIZED

    echo "maybe_string is ~{maybe_string}" >>wow_cool_i_am_CAPITALIZED
    echo "maybe_int is ~{maybe_int}" >>wow_cool_i_am_CAPITALIZED
    echo "definitely_int is ~{definitely_int}" >>wow_cool_i_am_CAPITALIZED
    echo "" >>wow_cool_i_am_CAPITALIZED
    echo "FA:" >>wow_cool_i_am_CAPITALIZED
    cat ~{maybe_file_array} >>wow_cool_i_am_CAPITALIZED
    echo "FA from struct:" >>wow_cool_i_am_CAPITALIZED
    cat ~{maybe_FA_from_struct} >>wow_cool_i_am_CAPITALIZED
    echo "here is some stdout for you"
  >>>
  output {
    File cap_out = "wow_cool_i_am_CAPITALIZED"
    File? nope = "wont_be_there"
    Array[File]? empty_file_array = leave_empty_file_array
  }
  runtime {
    willow_image_name: "default"
    docker: "none"
    memory: "120 MiB"
  }
}
