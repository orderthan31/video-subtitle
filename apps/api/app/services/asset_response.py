from fastapi import HTTPException
from fastapi.responses import FileResponse

from video_service.assets import AssetNotFoundError
from video_service.locking import download_lock


class AssetVideoResponse(FileResponse):
    def __init__(self, assets, asset_id, owner_id):
        self.assets, self.asset_id, self.owner_id = assets, asset_id, owner_id
        record = assets.read(asset_id, owner_id=owner_id)
        super().__init__(assets.source_path(record), filename=record['original_filename'],
                         content_disposition_type='inline')

    async def __call__(self, scope, receive, send):
        with download_lock(self.assets, self.asset_id):
            try:
                record = self.assets.read(self.asset_id, owner_id=self.owner_id)
            except AssetNotFoundError as exc:
                raise HTTPException(status_code=404, detail='Video not found.') from exc
            self.path = self.assets.source_path(record)
            if not self.path.is_file():
                raise HTTPException(status_code=404, detail='Video file is unavailable.')
            scope = {**scope, 'extensions': {key: value for key, value in scope.get('extensions', {}).items()
                                            if key != 'http.response.pathsend'}}
            await super().__call__(scope, receive, send)
