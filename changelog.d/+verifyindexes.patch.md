`verify-wheel` installs the built wheel with every index the project declares, so a wheel whose dependencies live on a declared index no longer needs `UV_INDEX` set on the job.
