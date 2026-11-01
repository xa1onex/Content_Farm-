from .base import PlatformPublisher
from ..errors import UIError
class InstagramPublisher(PlatformPublisher):
    platform='instagram'
    allowed_parameters={'caption','allow_comments'}
    def validate_parameters(self):
        if 'allow_comments' in self.parameters and not isinstance(self.parameters['allow_comments'],bool):
            raise UIError('invalid_publish_parameters')
