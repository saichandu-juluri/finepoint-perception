output "url" {
  description = "The server's address: what people type in the app's sign-in form."
  value       = "https://${var.domain != "" ? var.domain : aws_instance.server.public_ip}"
}

output "public_ip" {
  description = "The server's public IPv4. It changes if the server is stopped and started, not on a reboot."
  value       = aws_instance.server.public_ip
}

output "instance_id" {
  description = "The server's EC2 instance."
  value       = aws_instance.server.id
}

output "bucket" {
  description = "The bucket that holds the sessions, the database copy and the server's code."
  value       = aws_s3_bucket.sessions.bucket
}
