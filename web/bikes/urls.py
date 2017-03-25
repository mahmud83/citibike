from django.conf.urls import url

from . import views

urlpatterns = [
    url(r'^$', views.index, name='index'),
    url(r'^map/(?P<city>[a-z]+)$', views.map, name='map'),
]