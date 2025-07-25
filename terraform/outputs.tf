output "lambda_function_name" {
  value = aws_lambda_function.audio_stitch.function_name
}

output "bucket_name" {
  value = aws_s3_bucket.audio_bucket.id
}
