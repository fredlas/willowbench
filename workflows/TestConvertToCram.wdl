version 1.0

workflow TestConvertToCram {
  input { File input_bam
          File ref_fasta
          File ref_fasta_index
          String output_basename }
  call ConvertToCram { input: input_bam = input_bam,
                              ref_fasta = ref_fasta,
                              ref_fasta_index = ref_fasta_index,
                              output_basename = output_basename }
  output { File output_cram = ConvertToCram.output_cram
           File output_cram_index = ConvertToCram.output_cram_index
           File output_cram_md5 = ConvertToCram.output_cram_md5 }
}

task ConvertToCram {
  input {
    File input_bam
    File ref_fasta
    File ref_fasta_index
    String output_basename
    Int preemptible_tries = 3
  }

  command <<<
    #set -e
    #set -o pipefail
    #
    #samtools view -C -T ~{ref_fasta} ~{input_bam} | \
    #tee ~{output_basename}.cram | \
    #md5sum | awk '{print $1}' > ~{output_basename}.cram.md5
    #
    ## Create REF_CACHE. Used when indexing a CRAM
    #seq_cache_populate.pl -root ./ref/cache ~{ref_fasta}
    #export REF_PATH=:
    #export REF_CACHE=./ref/cache/%2s/%2s/%s
    #
    #samtools index ~{output_basename}.cram
    which samtools >~{output_basename}.cram
    echo yaaaaaaaaaaay >~{output_basename}.cram.crai
    echo lolchecksum >~{output_basename}.cram.md5
  >>>
  runtime {
    docker: "us.gcr.io/broad-gotc-prod/samtools:1.0.0-1.11-1624651616"
    preemptible: preemptible_tries
    memory: "3 GiB"
    cpu: "1"
  }
  output {
    File output_cram = "~{output_basename}.cram"
    File output_cram_index = "~{output_basename}.cram.crai"
    File output_cram_md5 = "~{output_basename}.cram.md5"
  }
}
