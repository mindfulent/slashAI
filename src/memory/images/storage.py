# slashAI - Discord Bot and MCP Server
# Copyright (c) 2025-2026 Slash Daemon slashdaemon@protonmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.
#
# Commercial licensing: [slashdaemon@protonmail.com]

"""
Image Storage - DigitalOcean Spaces integration for persistent image storage.

Images are stored with private ACL and accessed via signed URLs.
Structure: images/{user_id}/{year}/{month}/{hash}.{ext}
"""

import asyncio
import mimetypes
import os
from datetime import datetime
from typing import Optional

import boto3
from botocore.config import Config


class ImageStorage:
    """Manages image storage in DigitalOcean Spaces (S3-compatible)."""

    def __init__(
        self,
        spaces_key: Optional[str] = None,
        spaces_secret: Optional[str] = None,
        spaces_region: Optional[str] = None,
        spaces_bucket: Optional[str] = None,
    ):
        self.bucket = spaces_bucket or os.getenv("DO_SPACES_BUCKET", "slashai-images")
        self.region = spaces_region or os.getenv("DO_SPACES_REGION", "nyc3")

        key = spaces_key or os.getenv("DO_SPACES_KEY")
        secret = spaces_secret or os.getenv("DO_SPACES_SECRET")

        if not key or not secret:
            raise ValueError(
                "DO_SPACES_KEY and DO_SPACES_SECRET environment variables required"
            )

        self.client = boto3.client(
            "s3",
            region_name=self.region,
            endpoint_url=f"https://{self.region}.digitaloceanspaces.com",
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            config=Config(signature_version="s3v4"),
        )

    async def upload(
        self,
        image_bytes: bytes,
        user_id: int,
        file_hash: str,
        media_type: str,
    ) -> tuple[str, str]:
        """
        Upload image to DO Spaces.

        Args:
            image_bytes: Raw image data
            user_id: Discord user ID (for partitioning)
            file_hash: SHA-256 hash of the image
            media_type: MIME type (e.g., "image/png")

        Returns:
            Tuple of (storage_key, storage_url)
        """
        ext = mimetypes.guess_extension(media_type) or ".png"
        # Remove leading dot if present from some edge cases
        if ext.startswith("."):
            ext_clean = ext
        else:
            ext_clean = f".{ext}"

        date_prefix = datetime.utcnow().strftime("%Y/%m")
        key = f"images/{user_id}/{date_prefix}/{file_hash}{ext_clean}"

        # Run sync upload in thread pool
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=image_bytes,
            ContentType=media_type,
            ACL="private",
        )

        url = f"https://{self.bucket}.{self.region}.digitaloceanspaces.com/{key}"
        return key, url

    async def get_signed_url(self, key: str, expires_in: int = 3600) -> str:
        """
        Get a temporary signed URL for accessing an image.

        Args:
            key: Storage key of the image
            expires_in: URL validity in seconds (default 1 hour)

        Returns:
            Signed URL for temporary access
        """
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    async def delete(self, key: str) -> None:
        """Delete an image from storage."""
        await asyncio.to_thread(
            self.client.delete_object,
            Bucket=self.bucket,
            Key=key,
        )

    async def exists(self, key: str) -> bool:
        """Check if an image exists in storage."""
        try:
            await asyncio.to_thread(
                self.client.head_object,
                Bucket=self.bucket,
                Key=key,
            )
            return True
        except self.client.exceptions.ClientError:
            return False
