provider "aws" {
  region = var.region
}

resource "aws_s3_bucket" "audio_bucket" {
  bucket = var.bucket_name
}

resource "aws_iam_role" "lambda_exec_role" {
  name = "audio_stitch_lambda_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{
      Effect = "Allow",
      Principal = {
        Service = "lambda.amazonaws.com"
      },
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "lambda_policy" {
  role = aws_iam_role.lambda_exec_role.id
  policy = jsonencode({
    Version = "2012-10-17",
    Statement = [
      {
        Effect = "Allow",
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ],
        Resource = [
          "${aws_s3_bucket.audio_bucket.arn}",
          "${aws_s3_bucket.audio_bucket.arn}/*"
        ]
      },
      {
        Effect = "Allow",
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ],
        Resource = "*"
      }
    ]
  })
}

# Optional if you want to ZIP dynamically from local dir
# data "archive_file" "lambda_zip" {
#   type        = "zip"
#   source_dir  = "../lambda"
#   output_path = "../lambda.zip"
# }

resource "aws_lambda_function" "audio_stitch" {
  function_name = "audio_stitcher_lambda"
  role          = aws_iam_role.lambda_exec_role.arn
  handler       = "app.lambda_handler"
  runtime       = "python3.10"
  timeout       = 30
  filename      = "../lambda.zip"  # OR use data.archive_file if enabled
  source_code_hash = filebase64sha256("../lambda.zip")

  environment {
    variables = {
      LOG_LEVEL = "INFO"
    }
  }

  # Uncomment below if using FFmpeg Layer
  # layers = [aws_lambda_layer_version.ffmpeg_layer.arn]
  # depends_on = [aws_lambda_layer_version.ffmpeg_layer]
}

resource "aws_apigatewayv2_api" "api" {
  name          = "audio-api"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "lambda_integration" {
  api_id                = aws_apigatewayv2_api.api.id
  integration_type      = "AWS_PROXY"
  integration_uri       = aws_lambda_function.audio_stitch.invoke_arn
  integration_method    = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "lambda_route" {
  api_id    = aws_apigatewayv2_api.api.id
  route_key = "POST /stitch"
  target    = "integrations/${aws_apigatewayv2_integration.lambda_integration.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.api.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.audio_stitch.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.api.execution_arn}/*/*"  # <- Important fix
}

# Optional: FFmpeg Layer
# resource "aws_lambda_layer_version" "ffmpeg_layer" {
#   filename            = "../ffmpeg-layer.zip"
#   layer_name          = "ffmpeg"
#   compatible_runtimes = ["python3.10"]
#   source_code_hash    = filebase64sha256("../ffmpeg-layer.zip")
#   description         = "FFmpeg static binary layer for Python Lambda"
# }

output "invoke_url" {
  value = "${aws_apigatewayv2_api.api.api_endpoint}/stitch"
}
