#!/bin/bash\n# This script deploys the application on Cloud Run\n\n# Define variables\nPROJECT_ID=my-gcp-project\nSERVICE_NAME=my-cloud-run-service\nIMAGE_NAME=gcr.io/$PROJECT_ID/my-image\nREGION=us-central1\n\n# Build the Docker image\ndocker build -t $IMAGE_NAME .\n\n# Push the image to Google Container Registry\ndocker push $IMAGE_NAME\n\n# Deploy the service to Cloud Run\ngcloud run deploy $SERVICE_NAME \
    --image $IMAGE_NAME \
    --platform managed \
    --region $REGION \
    --allow-unauthenticated\n\necho "Deployment to Cloud Run complete!"