# Local Build: mzinga-apps on Minikube

This guide explains how to build the `mzinga-apps` backoffice image locally and deploy it to Minikube without pulling from `newesissrl.azurecr.io/mzinga/payload/gh/backoffice` since we have access to it.

---

## Prerequisites

- Minikube running (`minikube status`)
- Docker available in WSL
- Node.js source code in `mzinga/mzinga-apps/`

---

## Steps

### 1. Enter the project directory

```bash
cd ./Labs/mzinga/mzinga-apps
```

---

### 2. Point Docker to Minikube's daemon

This is the key step: instead of building on the local Docker daemon and copying the image over, you build directly inside Minikube's Docker daemon.

```bash
eval $(minikube docker-env)
```

> From this point on, all `docker` commands operate inside Minikube.
> When you close the terminal or want to switch back to your local Docker, run:
>
> ```bash
> eval $(minikube docker-env --unset)
> ```

---

### 3. Build the image

```bash
docker build \
  -f backoffice.Dockerfile \
  -t mzinga-backoffice:local \
  .
```

The build runs `npm ci` + `npm run build` inside the container. It may take a few minutes on the first run due to dependency installation.

To verify the image was built inside Minikube:

```bash
minikube ssh 'docker images | grep mzinga-backoffice'
```

---

### 4. Update `values.yaml`

In `mzinga-lab3/values.yaml`, we've updated the `mzingaApps` section:

```yaml
mzingaApps:
  image:
    repository: mzinga-backoffice
    tag: "local"
    pullPolicy: Never # local image — never attempt a registry pull
  replicaCount: 1
```

> `pullPolicy: Never` is required for local images in Minikube.
> Without it, Kubernetes will try to pull the image from a registry and fail.

---

### 5. Apply the update

```bash
helm upgrade mzinga-lab3 ./Labs/mzinga-lab3 \
  --namespace mzinga
```

Verify the pod becomes `1/1 Running`:

```bash
kubectl get pods -n mzinga -l app=mzinga-lab3-mzinga-apps
```

Test the health endpoint:

```bash
kubectl port-forward service/mzinga-lab3-mzinga-apps 3000:3000 -n mzinga &
sleep 2 && curl http://localhost:3000/probes/backoffice/health
```

Open the admin UI: [http://localhost:3000/admin](http://localhost:3000/admin)

---

## Rebuilding after code changes

When you change the source code, repeat steps 2–5:

```bash
eval $(minikube docker-env)

docker build \
  -f backoffice.Dockerfile \
  -t mzinga-backoffice:local \
  .

helm upgrade mzinga-lab3 /mnt/c/Users/STRVRL98S/Development/Labs/mzinga-lab3 \
  --namespace mzinga

# Force pod restart to pick up the new image
kubectl rollout restart deployment/mzinga-lab3-mzinga-apps -n mzinga
```

> Since the tag `local` does not change, you must explicitly restart the deployment
> to force Kubernetes to use the newly built image.
