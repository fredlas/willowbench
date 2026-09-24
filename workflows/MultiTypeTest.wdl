version 1.0

workflow MultiTypeTest {
  input {
    File the_input_file
    String an_input_str
    Int to_be_doubled
    Float to_be_tripled
  }
  call TheTask {
    input:
    file_in = the_input_file,
    the_str = an_input_str,
    the_int2 = to_be_doubled,
    the_float3 = to_be_tripled
  }
  output {
    File the_output = TheTask.capd_file_out
    String str_out = TheTask.str_out
    Int doubled = TheTask.int2_out
    Float tripled = TheTask.float3_out
  }
}

task TheTask {
  input {
    File file_in
    String the_str
    Int the_int2
    Float the_float3
  }
  command <<<
    THEFILEIN="~{file_in}"
    echo "file in path is $THEFILEIN"
    tr '[:lower:]' '[:upper:]' <"$THEFILEIN" >wow_cool_i_am_CAPITALIZED
    echo "here is some stdout for you: the_str is ~{the_str}"
    echo "oh and TODO TODO implement multiplication in my wdl parsing"
    echo "i am currently at: `pwd`"
  >>>
  output {
    File capd_file_out = "wow_cool_i_am_CAPITALIZED"
    String str_out = the_str
    Int int2_out = the_int2
    Float float3_out = the_float3
  }
}
