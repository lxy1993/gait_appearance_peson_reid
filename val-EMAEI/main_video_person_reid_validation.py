from __future__ import print_function, absolute_import
import os
import sys
import time
import datetime
import argparse
import os.path as osp
import numpy as np

import torch
import torch.nn as nn
import torch.backends.cudnn as cudnn
from torch.utils.data import DataLoader
from torch.autograd import Variable
from torch.optim import lr_scheduler
from tensorboardX import  SummaryWriter

import data_manager
from video_loader import VideoDataset
import transforms as T
import models
from models import resnet3d
from losses import CrossEntropyLabelSmooth, TripletLoss
from utils import AverageMeter, Logger, save_checkpoint
from eval_metrics import evaluate
from samplers import RandomIdentitySampler

parser = argparse.ArgumentParser(description='Train video model with cross entropy loss')
# Datasets
parser.add_argument('-d', '--dataset', type=str, default='mars',
                    choices=data_manager.get_names())
parser.add_argument('-j', '--workers', default=4, type=int,
                    help="number of data loading workers (default: 4)")
parser.add_argument('--height', type=int, default=224,
                    help="height of an image (default: 224)")
parser.add_argument('--width', type=int, default=112,
                    help="width of an image (default: 112)")
parser.add_argument('--seq-len', type=int, default=4, help="number of images to sample in a tracklet")
# Optimization options
parser.add_argument('--max-epoch', default=400, type=int,
                    help="maximum epochs to run")
parser.add_argument('--start-epoch', default=0, type=int,
                    help="manual epoch number (useful on restarts)")
parser.add_argument('--train-batch', default=16, type=int,
                    help="train batch size")
parser.add_argument('--test-batch', default=1, type=int, help="has to be 1")
parser.add_argument('--lr', '--learning-rate', default=0.0003, type=float,
                    help="initial learning rate, use 0.0001 for rnn, use 0.0003 for pooling and attention")
parser.add_argument('--stepsize', default=200, type=int,
                    help="stepsize to decay learning rate (>0 means this is enabled)")
parser.add_argument('--gamma', default=0.1, type=float,
                    help="learning rate decay")
parser.add_argument('--weight-decay', default=5e-04, type=float,
                    help="weight decay (default: 5e-04)")
parser.add_argument('--margin', type=float, default=0.3, help="margin for triplet loss")
parser.add_argument('--num-instances', type=int, default=4,
                    help="number of instances per identity")
parser.add_argument('--htri-only', action='store_true', default=False,
                    help="if this is True, only htri loss is used in training")
# Architecture
parser.add_argument('-a', '--arch', type=str, default='resnet50tp',
                    help="resnet503d, resnet50tp, resnet50ta, resnetrnn")
parser.add_argument('--pool', type=str, default='avg', choices=['avg', 'max'])

# Miscs
parser.add_argument('--print-freq', type=int, default=80, help="print frequency")
parser.add_argument('--seed', type=int, default=1, help="manual seed")
parser.add_argument('--pretrained-model', type=str,
                    default='/home/jiyang/Workspace/Works/video-person-reid/3dconv-person-reid/pretrained_models/resnet-50-kinetics.pth',
                    help='need to be set for resnet3d models')
parser.add_argument('--evaluate', action='store_true', help="evaluation only")
parser.add_argument('--eval-step', type=int, default=50,
                    help="run evaluation for every N epochs (set to -1 to test after training)")
parser.add_argument('--save-dir', type=str, default='log')
parser.add_argument('--use-cpu', action='store_true', help="use cpu")
parser.add_argument('--gpu-devices', default='0', type=str, help='gpu device ids for CUDA_VISIBLE_DEVICES')

args = parser.parse_args()


def main():
    file_name = "result_cl_v4.5.txt"
    result_file = open(file_name, "w+")
    if result_file:
        print("file exist")
    else:
        os.mkdir(file_name)

    torch.manual_seed(args.seed)
    os.environ['CUDA_VISIBLE_DEVICES'] = args.gpu_devices
    use_gpu = torch.cuda.is_available()
    if args.use_cpu: use_gpu = False

    if not args.evaluate:
        sys.stdout = Logger(osp.join(args.save_dir, 'log_train.txt'))
    else:
        sys.stdout = Logger(osp.join(args.save_dir, 'log_test.txt'))
    print("==========\nArgs:{}\n==========".format(args))

    if use_gpu:
        print("Currently using GPU {}".format(args.gpu_devices))
        cudnn.benchmark = True
        torch.cuda.manual_seed_all(args.seed)
    else:
        print("Currently using CPU (GPU is highly recommended)")

    print("Initializing dataset {}".format(args.dataset))
    dataset = data_manager.init_dataset(name=args.dataset)

    transform_train = T.Compose([
        T.Random2DTranslation(args.height, args.width),
        T.RandomHorizontalFlip(),
        T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    transform_val = T.Compose([
        T.Resize((args.height, args.width)),
        T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    transform_test = T.Compose([
        T.Resize((args.height, args.width)),
        T.ToTensor(),
        T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])
    pin_memory = True if use_gpu else False

    GEI_dir = "/media/work401/880AA9210AA90CEE/lxy/2020-03-15/MEAEI_npy"
    def load_GEI(GEI_dir, loader, batchsize,phase):
        dir = GEI_dir
        GEI_list = []
        GEI_npys = np.zeros((batchsize, 2048))

        for batch_idx, (imgs, pids, cids, seq_type) in enumerate(loader):
            cids = cids.to(torch.int8)
            for i in range(batchsize):
                if phase=="trian":
                    pid = "%03d" % (pids[i]+1)
                    # print("trian pid:",pid)
                elif phase=="val":
                    pid ="%03d" % (pids[i]+69)   #####pid, index
                    # print("val pid:", pid)
                else:
                    pid = "%03d" % pids[i]
                    # print("query pid:",pid)
                cid = "c" + str(cids[i].item())
                GEI_name = str(pid) + "-" + seq_type[i] + "-" + str(cid) + ".npy"
                GEI_name = os.path.join(dir, GEI_name)
                GEI_file = np.load(GEI_name).reshape(1, -1)
                GEI_npys[i] = GEI_file
            GEI_list.append(GEI_npys)

        return GEI_list

###########dataloader 加载数据集###########################
    trainloader = DataLoader(
        VideoDataset(dataset.train, seq_len=args.seq_len, sample='random', transform=transform_train),
        sampler=RandomIdentitySampler(dataset.train, num_instances=args.num_instances),
        batch_size=args.train_batch, num_workers=args.workers,
        pin_memory=pin_memory, drop_last=True,
    )

    valloader = DataLoader(
        VideoDataset(dataset.val, seq_len=args.seq_len, sample='random', transform=transform_val),
        sampler=RandomIdentitySampler(dataset.val, num_instances=args.num_instances),
        batch_size=args.train_batch, num_workers=args.workers,
        pin_memory=pin_memory, drop_last=True,
    )

    train_all={"train":trainloader,"val":valloader}

    queryloader = DataLoader(
        VideoDataset(dataset.query, seq_len=args.seq_len, sample='dense', transform=transform_test),
        batch_size=args.test_batch, shuffle=False, num_workers=args.workers,
        pin_memory=pin_memory, drop_last=False,
    )

    galleryloader = DataLoader(
        VideoDataset(dataset.gallery, seq_len=args.seq_len, sample='dense', transform=transform_test),
        batch_size=args.test_batch, shuffle=False, num_workers=args.workers,
        pin_memory=pin_memory, drop_last=False,
    )

    #######trainloader 中包含每个batchsize中轨迹的名称，接下来只要加载对应的GEI img########
    train_GEI = load_GEI(GEI_dir, trainloader, args.train_batch,"trian")
    print("finished train_GEI###########")
    val_GEI = load_GEI(GEI_dir, valloader, args.train_batch,"val")

    train_GEI_all={"train":train_GEI,"val":val_GEI}

    print("finished val_GEI###########")
    query_GEI = load_GEI(GEI_dir, queryloader, args.test_batch,"query")
    print("finished query_GEI ###########")
    gallery_GEI = load_GEI(GEI_dir, galleryloader, args.test_batch,"gallery")
    print("finished gallery_GEI ################")

    #####################加载模型，置置模型参数######################
    print("Initializing model: {}".format(args.arch))
    if args.arch == 'resnet503d':
        model = resnet3d.resnet50(num_classes=dataset.num_train_pids, sample_width=args.width,
                                  sample_height=args.height, sample_duration=args.seq_len)
        if not os.path.exists(args.pretrained_model):
            raise IOError("Can't find pretrained model: {}".format(args.pretrained_model))
        print("Loading checkpoint from '{}'".format(args.pretrained_model))
        checkpoint = torch.load(args.pretrained_model)
        state_dict = {}
        for key in checkpoint['state_dict']:
            if 'fc' in key: continue
            state_dict[key.partition("module.")[2]] = checkpoint['state_dict'][key]
        model.load_state_dict(state_dict, strict=False)
    else:
        model = models.init_model(name=args.arch, num_classes=dataset.num_train_pids, loss={'xent', 'htri'})
    print("Model size: {:.5f}M".format(sum(p.numel() for p in model.parameters()) / 1000000.0))

    criterion_xent = CrossEntropyLabelSmooth(num_classes=dataset.num_train_pids, use_gpu=use_gpu)
    criterion_htri = TripletLoss(margin=args.margin)

    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    if args.stepsize > 0:
        scheduler = lr_scheduler.StepLR(optimizer, step_size=args.stepsize, gamma=args.gamma)
    start_epoch = args.start_epoch

    if use_gpu:
        model = nn.DataParallel(model).cuda()

    if args.evaluate:
        print("Evaluate only")
        reslut = open("reslut.txt")
        test(model, queryloader, galleryloader, query_GEI, gallery_GEI, args.pool, use_gpu, reslut)
        return

#############训练、测试模型参数###############################
    start_time = time.time()
    best_rank1 = -np.inf
    if args.arch == 'resnet503d':
        torch.backends.cudnn.benchmark = False

    writer = SummaryWriter()
    for epoch in range(start_epoch, args.max_epoch):
        print("==> Epoch {}/{}".format(epoch + 1, args.max_epoch))

        loss_train,loss_val=train(model, criterion_xent, criterion_htri, optimizer, train_all, train_GEI_all, use_gpu)
        writer.add_scalars("data_loss", {"train_loss": loss_train.avg, "val_loss": loss_val.avg}, epoch)


        if args.stepsize > 0: scheduler.step()

        if args.eval_step > 0 and (epoch + 1) % args.eval_step == 0 or (epoch + 1) == args.max_epoch:
            print("==> Test")
            rank1 = test(model, queryloader, galleryloader, query_GEI, gallery_GEI, args.pool, use_gpu, result_file)
            writer.add_scalar("data/rank1",rank1,epoch)
            is_best = rank1 > best_rank1
            if is_best: best_rank1 = rank1

            if use_gpu:
                state_dict = model.module.state_dict()
            else:
                state_dict = model.state_dict()
            save_checkpoint({
                'state_dict': state_dict,
                'rank1': rank1,
                'epoch': epoch,
            }, is_best, osp.join(args.save_dir, 'checkpoint_ep' + str(epoch + 1) + '.pth.tar'))

    elapsed = round(time.time() - start_time)
    elapsed = str(datetime.timedelta(seconds=elapsed))
    writer.close()
    print("Finished. Total elapsed time (h:m:s): {}".format(elapsed))


def train(model, criterion_xent, criterion_htri, optimizer, trainloader, train_GEI, use_gpu):

    # loss_all ={}
    losses_train = AverageMeter()
    losses_val = AverageMeter()
    for phase in ["train","val"]:
        if phase=="train":
            model.train()
        else:
            model.eval()

        for batch_idx, (imgs, pids, camid, seqt_ype) in enumerate(trainloader[phase]):
            #####加载同一批次的GEI numpyValueError: too many values to unpack (expected 2) file
            GEI_features = train_GEI[phase][batch_idx]
            GEI_features = torch.from_numpy(GEI_features)
            if use_gpu:
                imgs, pids, GEI_features = imgs.cuda(), pids.cuda(), GEI_features.cuda()
            imgs, pids, GEI_features = Variable(imgs), Variable(pids).type(torch.LongTensor), Variable(GEI_features).float()

            #####数据和步态能量特征一同输入到网络中训练
            if phase=="val":
                with torch.no_grad():
                    outputs, features = model(imgs, GEI_features)
            else:
                outputs, features = model(imgs, GEI_features)

            if args.htri_only:
                # only use hard triplet loss to train the network
                loss = criterion_htri(features, pids)
            else:
                # combine hard triplet loss with cross entropy loss
                xent_loss = criterion_xent(outputs, pids)
                htri_loss = criterion_htri(features, pids)
                loss = xent_loss + htri_loss

            optimizer.zero_grad()
            if phase=="train":
                loss.backward()
                optimizer.step()
                losses_train.update(loss.data, pids.size(0))
                # losses.update(loss.data[0] pids.size(0))
            else:
                losses_val .update(loss.data, pids.size(0))

            if (batch_idx + 1) % args.print_freq == 0:
                print("Batch {}/{}\t Loss {:.6f} ({:.6f})".format(batch_idx + 1, len(trainloader), losses.val, losses.avg))
    return losses_train, losses_val


def test(model, queryloader, galleryloader, query_GEI, gallery_GEI, pool, use_gpu, result_file, ranks=[1, 5, 10, 20]):
    model.eval()
    qf, q_pids, q_camids, q_seq_types = [], [], [], []
    for batch_idx, (imgs, pids, camids, seq_types) in enumerate(queryloader):
        GEI_q_features = query_GEI[batch_idx]
        GEI_q_features = torch.from_numpy(GEI_q_features).squeeze().float()
        # print("pids:\n", pids, "camids:\n", camids, "seq_type:\n", seq_type)

        if use_gpu:
            imgs = imgs.cuda()

        with  torch.no_grad():
            imgs = Variable(imgs)

        # b=1, n=number of clips, s=16
        b, n, s, c, h, w = imgs.size()
        assert (b == 1)
        imgs = imgs.view(b * n, s, c, h, w)
        features = model(imgs)
        features = features.view(n, -1)
        features = torch.mean(features, 0)
        features = features.data.cpu()
        features = torch.cat((features, GEI_q_features), 0)
        qf.append(features)
        q_pids.extend(pids)
        q_camids.extend(camids)
        q_seq_types.extend(seq_types)

    qf = torch.stack(qf)
    q_pids = np.asarray(q_pids)
    q_camids = np.asarray(q_camids)
    q_seq_types =np.asarray(q_seq_types)
    #####当q_pid和q_camid相同时，可能存在seq_type不一样的情况，这点是否也要算进去?？?

    print("Extracted features for query set, obtained {}-by-{} matrix".format(qf.size(0), qf.size(1)))

    gf, g_pids, g_camids, g_seq_types = [], [], [],[]
    for batch_idx, (imgs, pids, camids, seq_types) in enumerate(galleryloader):
        GEI_g_features = gallery_GEI[batch_idx]
        GEI_g_features = torch.from_numpy(GEI_g_features).squeeze().float()

        if use_gpu:
            imgs = imgs.cuda()
        with torch.no_grad():
            imgs = Variable(imgs)
        b, n, s, c, h, w = imgs.size()
        imgs = imgs.view(b * n, s, c, h, w)
        assert (b == 1)
        features = model(imgs)
        features = features.view(n, -1)
        if pool == 'avg':
            features = torch.mean(features, 0)
        else:
            features, _ = torch.max(features, 0)

        # print("features.size #########",features.size())
        # print("GEI_g_features ########",GEI_g_features)
        features = features.data.cpu()
        features = torch.cat((features, GEI_g_features), 0)
        gf.append(features)
        g_pids.extend(pids)
        g_camids.extend(camids)
        g_seq_types.extend(seq_types)

    gf = torch.stack(gf)
    g_pids = np.asarray(g_pids)
    g_camids = np.asarray(g_camids)
    g_seq_types = np.asarray(g_seq_types)
    print("Extracted features for gallery set, obtained {}-by-{} matrix".format(gf.size(0), gf.size(1)))
    print("Computing distance matrix")

    m, n = qf.size(0), gf.size(0)
    distmat = torch.pow(qf, 2).sum(dim=1, keepdim=True).expand(m, n) + \
              torch.pow(gf, 2).sum(dim=1, keepdim=True).expand(n, m).t()
    distmat.addmm_(1, -2, qf, gf.t())
    distmat = distmat.numpy()

    print("Computing CMC and mAP")

    cmc, mAP = evaluate(distmat, q_pids, g_pids, q_camids, g_camids, q_seq_types, g_seq_types)

    print("Results ----------")
    result_file.write("Computing CMC and mAP" + "\n" + "Results ----------" + "\n")
    print("mAP: {:.1%}".format(mAP))
    print("CMC curve")
    result_file.write("mAP: {:.1%}".format(mAP) + "\n" + "CMC curve" + "\n")
    for r in ranks:
        print("Rank-{:<3}: {:.1%}".format(r, cmc[r - 1]))
        result_file.write("Rank-{:<3}: {:.1%}".format(r, cmc[r - 1]) + "\n" + "------------------" + "\n")
    print("------------------")

    return cmc[0]


if __name__ == '__main__':
    main()
