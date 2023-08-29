to activate venv in windows:
$ .\venv\Scripts\activate

to make firebase-admin work:
1. go to https://console.firebase.google.com/u/1/project/debate-center-dd720/settings/serviceaccounts/adminsdk
2. create a private key for python
3. put it in .\server file
4. make sure server.py is pointing to it