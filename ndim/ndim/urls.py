from django.conf.urls import patterns, include, url
# Uncomment the next two lines to enable the admin:
from django.contrib import admin
admin.autodiscover()

urlpatterns = patterns('ndim.views',
    url(r'^$', 'index' ,name='ndim-index'),
	url(r'^flatui/', include('flatui.urls',namespace='flatui')),
    url(r'^data.csv$','data',name='ndim-data'),
)
