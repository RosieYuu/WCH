# VTS (DSH with ViT Backbone - ICME 2022)
# paper [Vision Transformer Hashing for Image Retrieval, ICME 2022](https://arxiv.org/pdf/2109.12564.pdf)
# DSH basecode considered from https://github.com/swuxyj/DeepHash-pytorch

from utils.tools import *
import os
import torch
import torch.optim as optim
import time
import numpy as np
from TransformerModel.modeling import VisionTransformer, VIT_CONFIGS
import random
torch.multiprocessing.set_sharing_strategy('file_system')

def get_config():
    config = {
        "dataset": "cifar10",
        # "dataset": "coco",
        # "dataset": "nuswide_21",

        "net": VisionTransformer, "net_print": "ViT-B_16", "model_type": "ViT-B_16", "pretrained_dir": "pretrainedVIT/ViT-B_16.npz",
        
        "bit_list": [64,32,16],
        "optimizer": {"type": optim.Adam, "optim_params": {"lr": 1e-5}},
        "device": torch.device("cuda"), "save_path": "Checkpoints_Results",
        "epoch": 50, "test_map": 10, "batch_size": 32, "resize_size": 256, "crop_size": 224,
        "info": "WCH", "alpha": 0.1,
    }
    config = config_dataset(config)
    return config

def train_val(config, bit):
    start_epoch = 1
    Best_mAP = 0
    device = config["device"]
    train_loader, test_loader, dataset_loader, num_train, num_test, num_dataset = get_data(config)
    config["num_train"] = num_train
    
    num_classes = config["n_class"]
    hash_bit = bit
    
    if "ViT" in config["net_print"]:
        vit_config = VIT_CONFIGS[config["model_type"]]
        net = config["net"](vit_config, config["crop_size"], zero_head=True, num_classes=num_classes, hash_bit=hash_bit).to(device)
    else:
        net = config["net"](bit).to(device)
    
    if not os.path.exists(config["save_path"]):
        os.makedirs(config["save_path"])
    best_path = os.path.join(config["save_path"], config["dataset"] + "_" + config["info"] + "_" + config["net_print"] + "_Bit" + str(bit) + "-BestModel.pt")
    trained_path = os.path.join(config["save_path"], config["dataset"] + "_" + config["info"] + "_" + config["net_print"] + "_Bit" + str(bit) + "-IntermediateModel.pt")
    results_path = os.path.join(config["save_path"], config["dataset"] + "_" + config["info"] + "_" + config["net_print"] + "_Bit" + str(bit) + ".txt")
    f = open(results_path, 'a')
    
    if os.path.exists(trained_path):
        print('==> Resuming from checkpoint..')
        checkpoint = torch.load(trained_path)
        net.load_state_dict(checkpoint['net'])
        Best_mAP = checkpoint['Best_mAP']
        start_epoch = checkpoint['epoch'] + 1
    else:
        if "ViT" in config["net_print"]:
            print('==> Loading from pretrained model..')
            net.load_from(np.load(config["pretrained_dir"]))

    optimizer = config["optimizer"]["type"](net.parameters(), **(config["optimizer"]["optim_params"]))
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=config["epoch"])
    criterion = CL(config, bit)

    for epoch in range(start_epoch, config["epoch"]+1):
        current_time = time.strftime('%H:%M:%S', time.localtime(time.time()))
        print("%s-%s[%2d/%2d][%s] bit:%d, dataset:%s, training...." % (
            config["info"], config["net_print"], epoch, config["epoch"], current_time, bit, config["dataset"]), end="")
        net.train()
        train_loss = 0
        for image1, image2 in train_loader:
            image1, image2 = image1.to(device), image2.to(device)
            optimizer.zero_grad()
            h1, h2, weighted = net.train_forward(image1, image2)
            loss = criterion(h1, h2, weighted)
            train_loss += loss.item()
            loss.backward()
            optimizer.step()
        train_loss = train_loss / len(train_loader)
        scheduler.step()

        print("\b\b\b\b\b\b\b loss:%.3f" % (train_loss))
        f.write('Train | Epoch: %d | Loss: %.3f\n' % (epoch, train_loss))

        if (epoch) % config["test_map"] == 0:
            # print("calculating test binary code......")
            tst_binary, tst_label = compute_result(test_loader, net, device=device)

            # print("calculating dataset binary code.......")\
            trn_binary, trn_label = compute_result(dataset_loader, net, device=device)

            # print("calculating map.......")
            mAP = CalcTopMap(trn_binary.numpy(), tst_binary.numpy(), trn_label.numpy(), tst_label.numpy(),
                             config["topK"])
            
            if mAP > Best_mAP:
                Best_mAP = mAP
                P, R = pr_curve(trn_binary.numpy(), tst_binary.numpy(), trn_label.numpy(), tst_label.numpy())
                print(f'Precision Recall Curve data:\n"DSH":[{P},{R}],')
                f.write('PR | Epoch %d | ' % (epoch))
                for PR in range(len(P)):
                    f.write('%.5f %.5f ' % (P[PR], R[PR]))
                f.write('\n')
            
                print("Saving in ", config["save_path"])
                state = {
                    'net': net.state_dict(),
                    'Best_mAP': Best_mAP,
                    'epoch': epoch,
                }
                torch.save(state, best_path)
            print("%s epoch:%d, bit:%d, dataset:%s, MAP:%.3f, Best MAP: %.3f" % (
                config["info"], epoch, bit, config["dataset"], mAP, Best_mAP))
            f.write('Test | Epoch %d | MAP: %.3f | Best MAP: %.3f\n'
                % (epoch, mAP, Best_mAP))
            print(config)
        
            state = {
            	'net': net.state_dict(),
            	'Best_mAP': Best_mAP,
            	'epoch': epoch,
            }
            torch.save(state, trained_path)
    f.close()


class CL(torch.nn.Module):
    def __init__(self, config, bit):
        super(CL, self).__init__()
        self.ce = nn.CrossEntropyLoss()
        self.mse = nn.MSELoss()
        self.bit = bit

    def forward(self, h1, h2, weighted):
        logits = torch.einsum('ik,jk->ij', h1, h2)
        logits = logits / self.bit / 0.3

        balance_logits = h1.sum(0) / h1.size(0)
        reg = self.mse(balance_logits, torch.zeros_like(balance_logits)) - self.mse(h1, torch.zeros_like(h1))

        loss = self.ce(logits, weighted) + reg

        return loss


if __name__ == "__main__":
    config = get_config()
    print(config)
    for bit in config["bit_list"]:
        train_val(config, bit)

