# Code Citations

## License: unknown
https://github.com/matteogiorgi/toody-app/tree/228c4af9c85e77ddd9fec7c3998c477583b458e2/test-updates-5/app.py

```
.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).
```


## License: unknown
https://github.com/matteogiorgi/toody-app/tree/228c4af9c85e77ddd9fec7c3998c477583b458e2/test-updates-4/app.py

```
['GET', 'POST'])
def login():
    if request.method == 'POST':
        user = User.query.filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.
```

