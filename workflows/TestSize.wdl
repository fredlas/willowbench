workflow TestSize {
  input { File f }
  output {
    Float one_size = size(f)
    Float two_size = size([f, f])
    Float units_size = size(f, "KB")
  }
}
