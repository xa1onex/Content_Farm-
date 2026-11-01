from .base import PlatformPublisher
from ..errors import UIError
class TikTokPublisher(PlatformPublisher):
    platform='tiktok'
    allowed_parameters={'caption','visibility','allow_comments'}
    def validate_parameters(self):
        if 'visibility' in self.parameters and self.parameters['visibility'] not in ('public','friends','private'):
            raise UIError('invalid_publish_parameters')
        if 'allow_comments' in self.parameters and not isinstance(self.parameters['allow_comments'],bool):
            raise UIError('invalid_publish_parameters')
