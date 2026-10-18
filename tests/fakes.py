"""Test doubles shared across tests."""
import io


class _NoSuchKey(Exception):
    pass


class FakeS3:
    """In-memory stand-in for the boto3 S3 client calls the archive uses."""

    class exceptions:
        NoSuchKey = _NoSuchKey

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, **_):
        self.objects[Key] = Body

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=1000):
        keys = sorted(k for k in self.objects if k.startswith(Prefix))[:MaxKeys]
        return {"Contents": [{"Key": k} for k in keys]} if keys else {}

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise _NoSuchKey(Key)
        return {"Body": io.BytesIO(self.objects[Key])}
