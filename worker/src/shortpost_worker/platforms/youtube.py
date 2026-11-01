from .base import PlatformPublisher
from ..errors import UIError
class YouTubePublisher(PlatformPublisher):
    platform='youtube'
    allowed_parameters={'caption','title','visibility','made_for_kids'}
    def expected_publication_text(self):return self.context['title']
    def validate_parameters(self):
        if self.parameters.get('visibility') not in ('public','unlisted','private'):
            raise UIError('youtube_visibility_required')
        if not isinstance(self.parameters.get('made_for_kids'),bool):raise UIError('youtube_audience_required')
        if len(self.context['title'])>100:raise UIError('youtube_title_too_long')
