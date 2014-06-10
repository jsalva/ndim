from django.shortcuts import render

# Create your views here.
def index(request):
    return render(request,'index.html')

def data(request):
    return render(request,'data.csv')
