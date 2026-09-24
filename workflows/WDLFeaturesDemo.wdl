struct Foo {
  Int add_to
  Int expected
}

workflow WDLFeaturesDemo {
  input { Foo foo
          File tsv
          String success_msg }
  call AddToTsv { input: tsv_in = tsv, to_add = foo.add_to }
  scatter (r in AddToTsv.out) {
    scatter (x in r) {
      call TextNegate as tn { input: in = x }
    }
    Int inner_sum = sum(tn.out)
  }
  Int outer_sum = sum(inner_sum)
  if (outer_sum == foo.expected) {
    call Success { input: success_msg = success_msg }
  }
  output { Int the_sum = outer_sum }
}

task AddToTsv {
  input { File tsv_in
          Int to_add }
  command <<<
    awk -F'\t' -v offset="~{to_add}" '{for (i=1; i<=NF; i++) $i=$i+offset; OFS="\t"; print}' "~{tsv_in}" >tsv_out.tsv
  >>>
  output { Array[Array[String]] out = read_tsv("tsv_out.tsv") }
}

task TextNegate {
  input { String in }
  command <<<
    echo "-~{in}" >negated
    echo "the value is -~{in}"
    echo "the value in the file is:"
    cat negated
  >>>
  output { Int out = read_int("negated") }
}

task Success {
  input { String success_msg }
  command <<< echo "it worked! ~{success_msg}" >>>
}
